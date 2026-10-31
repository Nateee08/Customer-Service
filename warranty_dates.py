"""Use Smartsheet Status-cell history, never row modification or observation time."""
import datetime, re
from zoneinfo import ZoneInfo
PACIFIC=ZoneInfo('America/Los_Angeles')

def is_closed(value):
    text=str(value or '').strip()
    code=re.match(r'^(\d{3})(?=$|[\s–—-])',text)
    if code:return code.group(1) in {'030','031','032'}
    return bool(re.search(r'\b(?:closed|complete|completed)\b',text,re.I))

def qualifies_warranty(value):
    return bool(re.match(r'^030(?=$|[\s–—-])',str(value or '').strip()))

def closure_timestamp(history):
    entries=history.get('data',[])
    if len(entries)<history.get('totalCount',len(entries)):return None
    dated=[]
    for item in entries:
        try:
            dt=datetime.datetime.fromisoformat(item['modifiedAt'].replace('Z','+00:00'))
            if dt.tzinfo is None:return None
            dated.append((dt,item.get('value',item.get('displayValue'))))
        except (KeyError,ValueError,TypeError):return None
    dated.sort(key=lambda x:x[0],reverse=True)
    if not dated or not qualifies_warranty(dated[0][1]):return None
    # Use the start of the current 030 status period.
    start=dated[0][0]
    for dt,value in dated[1:]:
        if not qualifies_warranty(value):break
        start=dt
    return start.astimezone(datetime.timezone.utc).isoformat()

def anniversary(start):
    dt=datetime.datetime.fromisoformat(start).astimezone(PACIFIC)
    try:end=dt.replace(year=dt.year+1)
    except ValueError:end=dt.replace(year=dt.year+1,day=28)
    return end.isoformat()
