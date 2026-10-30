"""Use Smartsheet Status-cell history, never row modification or observation time."""
import datetime, re
from zoneinfo import ZoneInfo
PACIFIC=ZoneInfo('America/Los_Angeles')

def is_closed(value):
    return bool(re.search(r'\b(?:closed|complete|completed)\b', str(value or ''),re.I))

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
    if not dated or not is_closed(dated[0][1]):return None
    # Moving between closed subtypes doesn't restart coverage.
    start=dated[0][0]
    for dt,value in dated[1:]:
        if not is_closed(value):break
        start=dt
    return start.astimezone(datetime.timezone.utc).isoformat()

def anniversary(start):
    dt=datetime.datetime.fromisoformat(start).astimezone(PACIFIC)
    try:end=dt.replace(year=dt.year+1)
    except ValueError:end=dt.replace(year=dt.year+1,day=28)
    return end.isoformat()
