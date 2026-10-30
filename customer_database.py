"""Private, read-only client database matching. Never returns unverified records."""
import os, re, threading, time
import requests

CLIENT_SHEET_URL = 'https://app.smartsheet.com/sheets/hmJ84G7JvWfGcxxPvQ77f3VCcgjRRHVM3hmX94w1'
_lock = threading.Lock()
_sheet_id = None
_cache = None
_cache_until = 0
ALIASES = {
    'email': ['Email', 'Email Address', 'Customer Email', 'Homeowner Email'],
    'first_name': ['First Name', 'Customer First Name'],
    'last_name': ['Last Name', 'Customer Last Name'],
    'name': ['Customer Name', 'Homeowner Name', 'Full Name', 'Client Name'],
    'phone': ['Phone #', 'Phone', 'Phone Number', 'Customer Phone', 'Mobile Phone'],
    'job_number': ['Job#', 'Job #', 'Job Number', 'Job No', 'Project Number'],
    'address': ['Address', 'Street Address', 'Service Address', 'Project Address'],
    'address2': ['Address 2', 'Address Line 2', 'Unit', 'PO Box'],
    'city': ['City', 'Service City'], 'state': ['State', 'Service State'],
    'postal': ['Zip', 'Zip Code', 'Postal Code', 'ZIP/Postal Code'],
}

def norm(v): return re.sub(r'[^a-z0-9]', '', str(v).casefold())
def phone(v): return re.sub(r'\D', '', str(v))[-10:]
def emails(v): return {x.casefold() for x in re.findall(r'[^\s,;<>]+@[^\s,;<>]+', str(v))}

def get_sheet():
    global _sheet_id, _cache, _cache_until
    with _lock:
        if _cache is not None and time.time() < _cache_until: return _cache
        token = os.getenv('SMARTSHEET_TOKEN')
        if not token: raise RuntimeError('Client database unavailable')
        headers = {'Authorization': 'Bearer ' + token}
        base = 'https://api.smartsheet.com/2.0/sheets'
        if not _sheet_id:
            configured = os.getenv('SMARTSHEET_CLIENT_SHEET_ID')
            if configured and configured.isdigit(): _sheet_id = configured
            else:
                r = requests.get(base, headers=headers, params={'includeAll':'true'}, timeout=15)
                r.raise_for_status()
                matches = [s for s in r.json().get('data', []) if s.get('permalink','').split('?')[0].rstrip('/') == CLIENT_SHEET_URL]
                if len(matches) != 1: raise RuntimeError('Client database not shared with API account')
                _sheet_id = str(matches[0]['id'])
        r = requests.get(base+'/'+_sheet_id, headers=headers, params={'includeAll':'true'}, timeout=15)
        r.raise_for_status()
        data = r.json()
        if len(data.get('rows', [])) < data.get('totalRowCount', 0): raise RuntimeError('Incomplete client database read')
        _cache = data
        _cache_until = time.time()+60
        return data

def records(sheet):
    columns = {norm(c['title']):c['id'] for c in sheet.get('columns',[])}
    mapping = {}
    for key, aliases in ALIASES.items():
        found = [columns[norm(title)] for title in aliases if norm(title) in columns]
        found = list(dict.fromkeys(found))
        if len(found)==1: mapping[key]=found[0]
    if 'email' not in mapping: raise RuntimeError('Unique email column required for secure matching')
    for row in sheet.get('rows',[]):
        cells = {c['columnId']:c.get('displayValue',c.get('value','')) for c in row.get('cells',[])}
        d = {k:str(cells.get(cid,'') or '').strip() for k,cid in mapping.items()}
        if not d.get('name'): d['name']=' '.join(x for x in (d.get('first_name'),d.get('last_name')) if x)
        # Only explicit customer fields are ever copied; no notes, finances or other records.
        yield d

def match(sheet, verified_email, kind='email', value=None):
    if kind not in ('email','name','phone','job_number'): return None
    value = str(value or verified_email).strip()
    if not value: return None
    candidates = []
    for d in records(sheet):
        if verified_email.casefold() not in emails(d.get('email','')): continue
        if kind=='email': ok=value.casefold() in emails(d.get('email',''))
        elif kind=='phone': ok=len(phone(value))==10 and phone(d.get('phone',''))==phone(value)
        elif kind=='name': ok=len(value.split())>=2 and norm(d.get('name',''))==norm(value)
        else: ok=norm(d.get('job_number',''))==norm(value)
        if ok: candidates.append(d)
    # Multiple properties/jobs are never guessed, even if they share one mailbox.
    return candidates[0] if len(candidates)==1 else None

def intake_address(record):
    city = record.get('city','');state=record.get('state','');postal=record.get('postal','')
    locality = ', '.join(x for x in (city, ' '.join(x for x in (state,postal) if x)) if x)
    return '\n'.join(x for x in (record.get('address'),record.get('address2'),locality) if x)


def related_job_numbers(sheet, verified_email, customer_name, address):
    """Internal intake references only, matched by verified email AND name AND address."""
    if not customer_name or not address:return []
    jobs=set()
    for record in records(sheet):
        if verified_email.casefold() not in emails(record.get('email','')):continue
        if norm(record.get('name',''))!=norm(customer_name):continue
        if norm(intake_address(record))!=norm(address):continue
        if record.get('job_number'):jobs.add(record['job_number'])
    return sorted(jobs)
