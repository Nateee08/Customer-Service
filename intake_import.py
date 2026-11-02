"""Link staff-created intake rows to a verified account without copying staff notes."""
import datetime, hashlib, re, threading
from zoneinfo import ZoneInfo
from sqlalchemy import select, insert, update, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
import customer_database

_lock=threading.Lock()
_linked_versions={}

def cell_values(row,columns):
    cells={str(c['columnId']):c for c in row.get('cells',[])}
    return {key:(cells.get(str(cid),{}).get('value') if cells.get(str(cid),{}).get('value') is not None else cells.get(str(cid),{}).get('displayValue')) for key,cid in columns.items()}

def reference(value):
    """Staff owns the reference on imported rows; website prefixes apply only to website submissions."""
    text=str(value if value is not None else '').strip()
    return text or None

def created_at(row,values):
    raw=row.get('createdAt')
    if raw:
        try:
            dt=datetime.datetime.fromisoformat(raw.replace('Z','+00:00'))
            if dt.tzinfo:return dt.astimezone(datetime.timezone.utc).isoformat()
        except (ValueError,TypeError,AttributeError):pass
    try:return datetime.datetime.combine(datetime.date.fromisoformat(str(values['date_realized'])),datetime.time(),ZoneInfo('America/Los_Angeles')).isoformat()
    except (ValueError,KeyError):return datetime.datetime.now(datetime.timezone.utc).isoformat()

def link(a,user,snapshot):
    """Caller must authenticate the account's verified session before calling."""
    sheet=snapshot['sheet'];columns=snapshot['config'][2];sheet_id=snapshot['config'][1]
    if not {'email','status','subject','ticket'}.issubset(columns):return
    rows=sheet.get('rows')
    if not isinstance(rows,list) or len(rows)<sheet.get('totalRowCount',len(rows)):return
    email=(user.get('email') or '').strip().casefold()
    if not email:return
    with _lock,a.engine.begin() as c:
        key=(user['id'],str(sheet_id),email)
        version=sheet.get('version')
        if version is not None and _linked_versions.get(key)==version:return
        counts={'rows':len(rows),'matched':0,'created':0,'existing_web':0,'other_owner':0,'website_marker':0}
        matched_rows=[]
        for row in rows:
            values=cell_values(row,columns)
            if email not in customer_database.emails(values.get('email','')):continue
            counts['matched']+=1
            rid=str(row['id']);matched_rows.append(rid)
            existing=c.execute(select(a.tickets).where(a.tickets.c.row_id==rid)).mappings().first()
            if existing:
                if existing['user_id']!=user['id']:
                    counts['other_owner']+=1;continue
                if not c.execute(select(a.imported_tickets.c.ticket_id).where(a.imported_tickets.c.ticket_id==existing['id'])).scalar():
                    counts['existing_web']+=1;continue
                tid=existing['id']
                public={'subject':str(values.get('subject') or 'Service request'),
                    'name':' '.join(str(values.get(k) or '').strip() for k in ('first_name','last_name')).strip() or user.get('name',''),
                    'phone':customer_database.format_phone(values.get('phone')),'address':str(values.get('address') or '')}
                changed={k:v for k,v in public.items() if existing[k]!=v}
                if changed:c.execute(update(a.tickets).where(a.tickets.c.id==tid,a.tickets.c.user_id==user['id']).values(**changed))
            else:
                # A website write may already exist while its row link is committing.
                if re.search(r'(?m)^Website (?:request ID|ticket):\s*\S+',str(values.get('description') or '')):
                    counts['website_marker']+=1;continue
                tid='SM-'+hashlib.sha256((str(sheet_id)+':'+rid).encode()).hexdigest()[:32]
                if c.execute(select(a.tickets.c.id).where(a.tickets.c.id==tid)).scalar():continue
                c.execute(insert(a.tickets).values(id=tid,user_id=user['id'],row_id=rid,email=email,
                    name=' '.join(str(values.get(k) or '').strip() for k in ('first_name','last_name')).strip() or user.get('name',''),
                    phone=customer_database.format_phone(values.get('phone')),address=str(values.get('address') or ''),
                    category='Service',subject=str(values.get('subject') or 'Service request'),
                    description='Service request recorded by our service team.',created=created_at(row,values),status=str(values.get('status') or '000 - Intake')))
                counts['created']+=1
                c.execute(insert(a.imported_tickets).values(ticket_id=tid,sheet_id=str(sheet_id)))
                c.execute(insert(a.ticket_contacts).values(ticket_id=tid,first_name=str(values.get('first_name') or ''),last_name=str(values.get('last_name') or '')))
            reference_cell=next((cell for cell in row.get('cells',[]) if str(cell['columnId'])==str(columns['ticket'])),{})
            ref=reference(reference_cell.get('displayValue') if reference_cell.get('displayValue') is not None else values.get('ticket'))
            if ref:
                soc=re.fullmatch(r'(?:HPS-)?SOC-(\d+)',ref)
                if soc:
                    base='SOC-'+soc[1]
                    upsert=pg_insert if c.dialect.name=='postgresql' else sqlite_insert
                    c.execute(upsert(a.number_counters).values(name='soc',value=0).on_conflict_do_nothing())
                    c.execute(update(a.number_counters).where(a.number_counters.c.name=='soc',a.number_counters.c.value<int(soc[1])).values(value=int(soc[1])))
                    c.execute(upsert(a.customer_numbers).values(user_id=user['id'],base_number=base).on_conflict_do_nothing())
                saved=c.execute(select(a.request_numbers.c.ticket_id).where(a.request_numbers.c.ticket_id==tid)).scalar()
                if saved:c.execute(update(a.request_numbers).where(a.request_numbers.c.ticket_id==tid).values(reference=ref,synced=1))
                else:c.execute(insert(a.request_numbers).values(ticket_id=tid,reference=ref,synced=1))
            else:
                c.execute(delete(a.request_numbers).where(a.request_numbers.c.ticket_id==tid))
            manager_col=a.manager_column_id(sheet,columns)
            if manager_col is not None:
                saved=c.execute(select(a.ticket_assignments.c.ticket_id).where(a.ticket_assignments.c.ticket_id==tid)).scalar()
                manager=a.manager_value(row,manager_col)
                if saved:c.execute(update(a.ticket_assignments).where(a.ticket_assignments.c.ticket_id==tid).values(manager=manager))
                else:c.execute(insert(a.ticket_assignments).values(ticket_id=tid,manager=manager))
    a.app.logger.warning('Intake account import: account=%s version=%s counts=%s matched_rows=%s',hashlib.sha256(str(user['id']).encode()).hexdigest()[:12],version,counts,matched_rows)
    if version is not None:
        with _lock:
            if len(_linked_versions)>1000:_linked_versions.clear()
            _linked_versions[key]=version
