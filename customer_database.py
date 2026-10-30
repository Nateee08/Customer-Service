"""Read-only client matching, with separate customer and internal-intake policies."""
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
def format_phone(value):
    """Format a complete US number; preserve other numbers/extensions verbatim."""
    value = str(value or '').strip()
    if re.search(r'[^0-9+().\s-]', value): return value
    digits = re.sub(r'\D', '', value)
    if len(digits) == 11 and digits.startswith('1'): digits = digits[1:]
    if len(digits) != 10: return value
    return f'({digits[:3]}){digits[3:6]}-{digits[6:]}'
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


def _street(value):
    words = re.findall(r'[a-z0-9]+', str(value).casefold())
    aliases = {'street':'st', 'avenue':'ave', 'boulevard':'blvd', 'circle':'cir',
               'court':'ct', 'drive':'dr', 'lane':'ln', 'road':'rd', 'place':'pl',
               'terrace':'ter', 'highway':'hwy', 'parkway':'pkwy',
               'north':'n', 'south':'s', 'east':'e', 'west':'w',
               'apartment':'unit', 'apt':'unit', 'suite':'unit', 'ste':'unit'}
    return ' '.join(aliases.get(word, word) for word in words)


def _same_property(record, submitted_address):
    """Compare the full street/unit and locality, allowing standard street abbreviations."""
    lines = [line.strip() for line in str(submitted_address).splitlines() if line.strip()]
    if len(lines) < 2 or not record.get('address'):
        return False
    street = ' '.join(lines[:-1])
    expected = ' '.join(filter(None, [record.get('address'), record.get('address2')]))
    if _street(street) != _street(expected):
        return False
    locality = lines[-1]
    city = locality.split(',')[0].strip()
    if record.get('city') and norm(city) != norm(record['city']):
        return False
    zip_match = re.search(r'\b(\d{5})(?:-\d{4})?\b', locality)
    if record.get('postal') and zip_match and record['postal'][:5] != zip_match.group(1):
        return False
    state_match = re.search(r',\s*([A-Za-z]{2})\b', locality)
    if record.get('state') and state_match and norm(record['state']) != norm(state_match.group(1)):
        return False
    return bool(record.get('city') or (record.get('postal') and zip_match))


def intake_lookup(sheet, *, email, name, telephone, address, job_number=''):
    """Internal staff intake enrichment only. Never use for a customer lookup response.

    Exact email, phone, full-name, job or property matches can identify a record.
    Other matching details break ties; a first name alone is never sufficient.
    Multiple jobs for one person/property remain explicit references. This does
    not grant account access, and the public match() stays email-bound.
    """
    candidates = []
    submitted_name = norm(name)
    submitted_phone = phone(telephone)
    requested_job = norm(job_number) if job_number else ''
    for record in records(sheet):
        if requested_job and norm(record.get('job_number', '')) != requested_job:
            continue
        full_name = norm(record.get('name', ''))
        first_name = norm(record.get('first_name', ''))
        full_name_match = bool(submitted_name and len(name.split()) >= 2 and submitted_name == full_name)
        first_name_match = bool(submitted_name and submitted_name == first_name)
        phone_match = len(submitted_phone) == 10 and submitted_phone == phone(record.get('phone', ''))
        email_match = bool(email and email.casefold() in emails(record.get('email', '')))
        address_match = _same_property(record, address)
        score = sum((full_name_match, phone_match, email_match, address_match, bool(requested_job)))
        if score:
            candidates.append((record, (score, int(first_name_match))))
    if not candidates:
        return {'record': None, 'job_numbers': []}
    strongest = max(score for _, score in candidates)
    leaders = [r for r, score in candidates if score == strongest]
    def identity(record):
        return (norm(record.get('name', '')),
                _street(' '.join(filter(None, [record.get('address'), record.get('address2')]))),
                norm(record.get('city', '')), record.get('postal', '')[:5])
    identities = {identity(r) for r in leaders}
    if len(identities) != 1:
        return {'record': None, 'job_numbers': []}
    chosen = next(iter(identities))
    if not chosen[0]:
        return {'record': None, 'job_numbers': []}
    # Include all exact customer/property job references, even when an older
    # duplicate client row has a different or missing email/phone value.
    candidates = [r for r in records(sheet) if identity(r) == chosen]
    # Duplicate client rows are harmless; conflicting name fields are not guessed.
    result = {}
    for key in ('name', 'first_name', 'last_name'):
        values = {r.get(key, '').strip() for r in candidates if r.get(key, '').strip()}
        if len({norm(v) for v in values}) > 1:
            return {'record': None, 'job_numbers': []}
        result[key] = sorted(values)[0] if values else ''
    # Include this customer's other properties only with the same full name and
    # the verified account email. A shared name/phone alone cannot expose jobs.
    account_jobs = [r for r in records(sheet) if norm(r.get('name', '')) == chosen[0]
                    and email and email.casefold() in emails(r.get('email', ''))]
    jobs = sorted({r['job_number'] for r in candidates + account_jobs if r.get('job_number')})
    return {'record': result, 'job_numbers': jobs}
