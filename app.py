import os,json,base64,secrets,hashlib,time,datetime,smtplib,io,re,threading
from zoneinfo import ZoneInfo
from pathlib import Path
from email.message import EmailMessage
from urllib.parse import urlsplit
from flask import Flask,request,jsonify,render_template,redirect,make_response,send_file,abort
from werkzeug.security import generate_password_hash,check_password_hash
from sqlalchemy import create_engine,MetaData,Table,Column,String,Integer,Text,LargeBinary,select,insert,update,delete,and_,func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
import requests
import notifications
import qa_pdf
import account_security
import customer_database
import warranty_dates
import ticket_numbers
import intake_columns
from sheet_snapshot import SheetSnapshot
import sys
app=Flask(__name__)
app.config['MAX_CONTENT_LENGTH']=22*1024*1024
meta=MetaData()
email_outbox=notifications.define_table(meta)
users=Table('care_users',meta,Column('id',String,primary_key=True),Column('email',String,unique=True,nullable=False),Column('name',String,nullable=False),Column('password',Text,nullable=False),Column('admin',Integer,nullable=False,default=0),Column('plan',String,nullable=False,default='None'),Column('membership_start',String),Column('membership_end',String),Column('minutes_used',Integer,nullable=False,default=0),Column('completion_date',String))
sessions=Table('care_verified_sessions',meta,Column('token',String,primary_key=True),Column('user_id',String,nullable=False),Column('expires',Integer,nullable=False))
tickets=Table('care_tickets',meta,Column('id',String,primary_key=True),Column('user_id',String,nullable=False),Column('email',String,nullable=False),Column('name',String),Column('phone',String),Column('address',String),Column('category',String),Column('subject',String),Column('description',Text),Column('created',String),Column('status',String),Column('row_id',String),Column('queue',Integer))
ticket_details=Table('care_ticket_details',meta,Column('ticket_id',String,primary_key=True),Column('job_number',String))
ticket_contacts=Table('care_ticket_contacts',meta,Column('ticket_id',String,primary_key=True),Column('first_name',String),Column('last_name',String))
ticket_assignments=Table('care_ticket_assignments',meta,Column('ticket_id',String,primary_key=True),Column('manager',String))
intake_attempts=Table('care_intake_attempts',meta,Column('ticket_id',String,primary_key=True),Column('attempted',Integer,nullable=False,default=0))
column_backups=Table('care_column_migration_backups',meta,Column('id',String,primary_key=True),Column('created',String),Column('payload',Text,nullable=False))
number_tables=ticket_numbers.define_tables(meta)
customer_numbers,request_numbers,number_counters=number_tables
warranties=Table('care_workmanship_dates',meta,Column('ticket_id',String,primary_key=True),Column('user_id',String,nullable=False),Column('started_at',String,nullable=False),Column('checked_version',String))
closures=Table('care_ticket_closures',meta,Column('ticket_id',String,primary_key=True),Column('closed_at',Integer,nullable=False))
files=Table('care_files',meta,Column('id',String,primary_key=True),Column('ticket_id',String,nullable=False),Column('user_id',String,nullable=False),Column('name',String),Column('type',String),Column('data',LargeBinary),Column('synced',Integer,default=0))
reviews=Table('care_reviews',meta,Column('id',String,primary_key=True),Column('ticket_id',String),Column('user_id',String),Column('data',Text),Column('created',String))
challenges=account_security.define_table(meta)
account_deliveries=account_security.define_delivery_table(meta)
resets=Table('care_resets',meta,Column('token',String,primary_key=True),Column('user_id',String),Column('expires',Integer))
limits=Table('care_limits',meta,Column('key',String,primary_key=True),Column('count',Integer,nullable=False))
url=os.getenv('DATABASE_URL','')
if url.startswith('postgres://'):url=url.replace('postgres://','postgresql+psycopg://',1)
elif url.startswith('postgresql://'):url=url.replace('postgresql://','postgresql+psycopg://',1)
if not url and os.getenv('DATA_DIR'):
 os.makedirs(os.environ['DATA_DIR'],exist_ok=True);url='sqlite:///'+os.path.join(os.environ['DATA_DIR'],'customer-care.db')
db_options={'pool_pre_ping':True}
if url.startswith('postgresql'):
 db_options.update(pool_timeout=5,connect_args={'connect_timeout':5,'options':'-c statement_timeout=10000 -c lock_timeout=5000'})
engine=create_engine(url,**db_options) if url else None
if engine:meta.create_all(engine)
DOCS=json.load(open(os.path.join(os.path.dirname(__file__),'documents.json')))
def digest(s):return hashlib.sha256(s.encode()).hexdigest()
def secure_cookies():return bool(os.getenv('RENDER'))
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def db_required():
 if engine is None:abort(503,description='Customer accounts are not enabled yet. Durable storage must be connected first.')
def current_user():
 if not engine:return None
 token=request.cookies.get('care_session','')
 if not token:return None
 with engine.connect() as c:return c.execute(select(users).join(sessions,users.c.id==sessions.c.user_id).where(sessions.c.token==digest(token),sessions.c.expires>int(time.time()))).mappings().first()
def required():
 db_required();u=current_user()
 if not u:abort(401,description='Please sign in.')
 return u
def staff():
 u=required()
 if not u['admin']:abort(403)
 return u
def csrf_ok():
 origin=request.headers.get('Origin')
 host=request.headers.get('X-Forwarded-Host',request.host)
 if origin and urlsplit(origin).netloc==host:return True
 return bool(request.form.get('csrf') and secrets.compare_digest(request.form.get('csrf',''),request.cookies.get('care_csrf','')))
@app.before_request
def check_write():
 if request.method in ('POST','PUT','DELETE') and not csrf_ok():abort(403,description='Please reload the page and try again.')
@app.after_request
def headers(r):
 r.headers['X-Content-Type-Options']='nosniff';r.headers['Referrer-Policy']='same-origin';r.headers['X-Frame-Options']='DENY'
 if request.path=='/' or request.path.startswith(('/api','/login','/register','/staff','/forgot','/reset','/verify-account')):r.headers['Cache-Control']='no-store'
 return r
@app.errorhandler(413)
def too_large(e):return jsonify(error='Attachments must total less than 20 MB.'),413
@app.errorhandler(SQLAlchemyError)
def database_unavailable(e):
 app.logger.error('Database request failed: %s',type(e).__name__)
 if request.path.startswith('/api/'):return jsonify(error='The service is temporarily busy. Please try again shortly.'),503
 return auth_view('login','The service is temporarily busy. Please try again shortly. Your account information is safe.',503)
@app.errorhandler(401)
@app.errorhandler(403)
@app.errorhandler(400)
@app.errorhandler(503)
def problem(e):
 if request.path.startswith('/api/'):return jsonify(error=e.description),e.code
 return render_template('auth.html',title='Customer care',message=e.description,enabled=False,mode='login'),e.code
@app.get('/')
def home():
 assets=b''.join((Path(app.root_path)/'static'/name).read_bytes() for name in ('portal.js','portal.css'))
 return render_template('index.html',asset_version=hashlib.sha256(assets).hexdigest()[:16])
@app.get('/favicon.ico')
def favicon():return send_file(Path(app.static_folder)/'favicon.ico',mimetype='image/vnd.microsoft.icon',max_age=86400)
@app.get('/healthz')
def health():return {'status':'ok','storageConfigured':bool(engine)}
@app.get('/api/me')
def me():
 u=current_user();return {'user':{'name':u['name'],'email':u['email'],'admin':bool(u['admin'])} if u else None}
@app.get('/documents/<name>')
def document(name):
 if name not in DOCS:abort(404)
 with open(os.path.join(os.path.dirname(__file__),DOCS[name])) as f:data=base64.b64decode(f.read())
 return send_file(io.BytesIO(data),mimetype='application/pdf',download_name=name,as_attachment=request.args.get('download')=='1')
def rate(key,max_count=10):
 bucket=str(int(time.time())//900);key=digest(key+bucket)
 with engine.begin() as c:
  row=c.execute(select(limits.c.count).where(limits.c.key==key)).scalar()
  if row is None:
   try:c.execute(insert(limits).values(key=key,count=1))
   except IntegrityError:abort(429)
  else:
   if row>=max_count:abort(429,description='Too many attempts. Try again in 15 minutes.')
   c.execute(update(limits).where(limits.c.key==key).values(count=limits.c.count+1))
def auth_view(mode,message='',status=200):
 token=request.cookies.get('care_csrf') or secrets.token_urlsafe(32)
 title={'login':'Sign in','register':'Create account','forgot':'Reset password','reset':'Set new password'}[mode]
 if engine is None:message='Customer accounts are being set up. Please contact our service team for help.'
 r=make_response(render_template('auth.html',title=title,mode=mode,csrf=token,message=message,enabled=engine is not None),status);r.set_cookie('care_csrf',token,httponly=True,secure=bool(os.getenv('RENDER')),samesite='Lax');return r
@app.route('/login',methods=['GET','POST'])
@app.route('/register',methods=['GET','POST'])
def auth():
 mode='register' if request.path=='/register' else 'login'
 if request.method=='GET':return auth_view(mode)
 db_required();rate('auth-origin:'+str(request.remote_addr),100);email=request.form.get('email','').strip().lower();password=request.form.get('password','');rate('auth:'+email)
 if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email) or len(email)>254 or not 12<=len(password)<=128:return auth_view(mode,'Use a valid email and a password of 12–128 characters.',400)
 with engine.begin() as c:
  u=c.execute(select(users).where(users.c.email==email)).mappings().first()
  if mode=='register':
   name=request.form.get('name','').strip()
   if not name or len(name)>200:return auth_view(mode,'Please enter your name.',400)
   if u:
    if not check_password_hash(u['password'],password):return auth_view(mode,'Unable to create this account. Try signing in or resetting your password.',400)
    uid=u['id']
   else:
    uid=secrets.token_hex(16)
    try:c.execute(insert(users).values(id=uid,email=email,name=name,password=generate_password_hash(password),admin=0,plan='None',minutes_used=0))
    except IntegrityError:return auth_view(mode,'Unable to create this account. Try signing in.',400)
  else:
   if not u or not check_password_hash(u['password'],password):return auth_view(mode,'Email or password is incorrect.',400)
   uid=u['id']
 return start_verification(uid,email)
@app.get('/logout')
def logout():
 if engine:
  with engine.begin() as c:c.execute(delete(sessions).where(sessions.c.token==digest(request.cookies.get('care_session',''))))
 r=redirect('/');r.delete_cookie('care_session');return r
@app.route('/forgot-password',methods=['GET','POST'])
def forgot():
 if request.method=='GET':return auth_view('forgot')
 db_required()
 if not notifications.configured() or not os.getenv('PUBLIC_URL'):return auth_view('forgot','Password reset email is not configured yet. Please contact customer service.',503)
 email=request.form.get('email','').strip().lower();rate('reset:'+email,3)
 with engine.begin() as c:
  u=c.execute(select(users).where(users.c.email==email)).mappings().first()
  if u:
   token=secrets.token_urlsafe(32);c.execute(insert(resets).values(token=digest(token),user_id=u['id'],expires=int(time.time())+1800))
   try:
    notifications.send_account_email(email,'Reset your Hooked on Solar password','Use this link within 30 minutes to reset your password:\n'+os.environ['PUBLIC_URL'].rstrip('/')+'/reset-password/'+token)
   except Exception:return auth_view('forgot','Reset email could not be sent. Please contact customer service.',503)
 return auth_view('forgot','If that account exists, a reset link has been sent.')
@app.route('/reset-password/<token>',methods=['GET','POST'])
def reset(token):
 db_required()
 with engine.begin() as c:
  row=c.execute(select(resets).where(resets.c.token==digest(token),resets.c.expires>int(time.time()))).mappings().first()
  if not row:return auth_view('forgot','This reset link is invalid or expired.',400)
  if request.method=='GET':return auth_view('reset')
  password=request.form.get('password','')
  if not 12<=len(password)<=128:return auth_view('reset','Use 12–128 characters.',400)
  c.execute(update(users).where(users.c.id==row['user_id']).values(password=generate_password_hash(password)));c.execute(delete(resets).where(resets.c.user_id==row['user_id']));c.execute(delete(sessions).where(sessions.c.user_id==row['user_id']));c.execute(delete(challenges).where(challenges.c.user_id==row['user_id']))
 return redirect('/login')
_intake_lock=threading.RLock()
_intake_attachment_lock=threading.Lock()
_intake_delivery_slots=threading.BoundedSemaphore(4)
_intake_columns=None
_intake_retry_at=0
def smartsheet_config():
 global _intake_columns,_intake_retry_at
 token=os.getenv('SMARTSHEET_TOKEN');sheet=os.getenv('SMARTSHEET_SHEET_ID')
 if not token or not sheet:return None
 # Cached read configuration must not wait behind ticket/attachment transfers.
 if _intake_columns:return token,sheet,_intake_columns
 with _intake_lock:
  if _intake_columns:return token,sheet,_intake_columns
  if time.time()<_intake_retry_at:return None
  try:
   explicit=json.loads(os.getenv('SMARTSHEET_COLUMNS','{}'))
   base='https://api.smartsheet.com/2.0/sheets/'+sheet
   headers={'Authorization':'Bearer '+token}
   r=requests.get(base+'/columns',headers=headers,timeout=15);r.raise_for_status()
   columns=r.json()['data'];by_title={c['title'].strip().casefold():c for c in columns}
   mapping={'first_name':'First Name','last_name':'Last Name','email':'Email','phone':'Phone #','address':'Address','description':'Notes:','status':'Status','date_realized':'Date Realized'}
   missing=[title for title in mapping.values() if title.casefold() not in by_title]
   if missing:raise ValueError('Required intake columns missing: '+', '.join(missing))
   for key,title in [('submitted_at','Website Submitted At')]:
    if title.casefold() not in by_title:
     try:
      r=requests.post(base+'/columns',headers=headers,json=[{'title':title,'type':'TEXT_NUMBER','index':len(columns)}],timeout=15);r.raise_for_status()
      added=r.json()['result'];columns.extend(added)
      by_title.update({c['title'].strip().casefold():c for c in added})
     except (requests.RequestException,ValueError,KeyError) as exc:
      if key!='submitted_at':raise
      app.logger.warning('Submission time retained in intake notes; timestamp column unavailable (HTTP %s)',getattr(getattr(exc,'response',None),'status_code','unavailable'))
      continue
    mapping[key]=title
   job_aliases={customer_database.norm(title) for title in customer_database.ALIASES['job_number']}
   job_title=next((col['title'] for col in columns if customer_database.norm(col['title']) in job_aliases),None)
   if not job_title or 'issue' not in by_title:raise ValueError('Intake needs Job # and Issue columns')
   mapping['ticket']=job_title;mapping['subject']=by_title['issue']['title']
   manager_title=next((col['title'] for col in columns if re.search(r'\bmanagers?\b',col['title'].strip(),re.I)),None)
   if manager_title:mapping['manager']=manager_title
   _intake_columns={key:by_title[title.casefold()]['id'] for key,title in mapping.items()}
   _intake_columns.update({key:value for key,value in explicit.items() if key in _intake_columns and key not in ('ticket','subject')})
   app.logger.warning('Smartsheet intake mapping ready: columns=%s manager_column=%s',json.dumps([{k:c.get(k) for k in ('id','title','type')} for c in columns]),_intake_columns.get('manager'))
   return token,sheet,_intake_columns
  except (requests.RequestException,ValueError,KeyError) as exc:
   _intake_retry_at=time.time()+60
   app.logger.warning('Smartsheet intake setup failed; will retry (%s, HTTP %s)',type(exc).__name__,getattr(getattr(exc,'response',None),'status_code','unavailable'))
   return None

def smart_request(method,path,**kwargs):
 cfg=smartsheet_config();return requests.request(method,'https://api.smartsheet.com/2.0/sheets/'+cfg[1]+path,headers={'Authorization':'Bearer '+cfg[0],**kwargs.pop('headers',{})},timeout=kwargs.pop('timeout',15),**kwargs)
_portal_snapshot=SheetSnapshot(ttl=15)
_portal_background=False
def load_portal_snapshot():
 started=time.monotonic()
 try:
  cfg=smartsheet_config()
  if not cfg:raise RuntimeError('Intake connection unavailable')
  # Only rows already delivered before this read can be judged missing from it.
  # This also protects a new ticket from an older cached or in-flight sheet read.
  with engine.connect() as c:
   known_rows=dict(c.execute(select(tickets.c.id,tickets.c.row_id).where(tickets.c.row_id.is_not(None))).all())
  r=smart_request('GET','',params={'includeAll':'true'},timeout=(3,6));r.raise_for_status()
  sheet=r.json();rows=sheet.get('rows')
  if not isinstance(rows,list) or len(rows)<sheet.get('totalRowCount',len(rows)):raise ValueError('Incomplete intake sheet')
  reconcile_deleted_tickets(sheet,known_rows,cfg[2]['status'],cfg[2])
  return dict(sheet=sheet,config=cfg,known_rows=known_rows,checked_at=now())
 except Exception as exc:
  app.logger.warning('Portal status refresh deferred after %.2fs (%s); saved tickets retained',time.monotonic()-started,type(exc).__name__)
  raise
def manager_column_id(sheet,columns):
 return next((col['id'] for col in sheet.get('columns',[]) if re.search(r'\bmanagers?\b',col.get('title','').strip(),re.I)),(columns or {}).get('manager'))

def manager_value(row,column):
 cell=next((cell for cell in row.get('cells',[]) if str(cell['columnId'])==str(column)),{})
 value=cell.get('displayValue') or cell.get('value')
 if not isinstance(value,(str,int,float)):
  obj=cell.get('objectValue') or {}
  value=', '.join(str(x.get('name') or x.get('email') or '') for x in obj.get('values',[]) if isinstance(x,dict)) or obj.get('name') or obj.get('email')
 return str(value) if value else None

def reconcile_deleted_tickets(sheet,known_rows,status_column=None,columns=None):
 # Reconcile each private request/row pair, not the shared customer number.
 rows=sheet.get('rows')
 if not isinstance(rows,list) or len(rows)<sheet.get('totalRowCount',len(rows)):
  raise ValueError('Incomplete intake sheet')
 present={str(row['id']):row for row in rows}
 # Clearing cells leaves a valid row ID in Smartsheet. Treat a completely
 # cleared intake row like deletion, but only with a complete known mapping.
 if columns and {'first_name','last_name','email','phone','address','description','ticket','subject','status'}.issubset(columns):
  def populated(row):
   cells={str(cell['columnId']):cell.get('value') for cell in row.get('cells',[])}
   return any(cells.get(str(cid)) not in (None,'') for cid in columns.values())
  present={rid:row for rid,row in present.items() if populated(row)}
 removed=[]
 manager_column=manager_column_id(sheet,columns)
 with engine.begin() as c:
  for tid,row_id in known_rows.items():
   if not row_id:continue
   if str(row_id) in present:
    # Restore only from a new successful read, never an older cached view.
    cells={str(cell['columnId']):cell.get('value') for cell in present[str(row_id)].get('cells',[])}
    status=cells.get(str(status_column))
    if status:c.execute(update(tickets).where(tickets.c.id==tid,tickets.c.row_id==row_id,tickets.c.status!=str(status)).values(status=str(status)))
    if manager_column is not None and c.execute(select(tickets.c.id).where(tickets.c.id==tid,tickets.c.row_id==row_id)).scalar():
     manager=manager_value(present[str(row_id)],manager_column)
     saved=c.execute(select(ticket_assignments).where(ticket_assignments.c.ticket_id==tid)).mappings().first()
     if saved is None:c.execute(insert(ticket_assignments).values(ticket_id=tid,manager=manager))
     elif saved['manager']!=manager:c.execute(update(ticket_assignments).where(ticket_assignments.c.ticket_id==tid).values(manager=manager))
    continue
   result=c.execute(update(tickets).where(tickets.c.id==tid,tickets.c.row_id==row_id,tickets.c.status!='Deleted from Smartsheet').values(status='Deleted from Smartsheet'))
   if result.rowcount:removed.append(tid)
 if removed:app.logger.warning('Intake deletion sync: removed %s website requests: %s',len(removed),', '.join(removed))
 app.logger.info('Intake reconciliation: sheet_rows=%s linked_requests=%s matched=%s missing=%s',len(rows),len(known_rows),sum(str(row) in present for row in known_rows.values()),sum(str(row) not in present for row in known_rows.values()))

_warranty_lock=threading.Lock()
def refresh_warranties(sheet_data,cfg,owned):
 if not _warranty_lock.acquire(blocking=False):return
 try:
  by_id={str(row['id']):row for row in sheet_data.get('rows',[])}
  for t in owned:
   row=by_id.get(t['row_id'])
   if not row:continue
   status=next((cell.get('value') for cell in row.get('cells',[]) if str(cell['columnId'])==str(cfg[2]['status'])),None)
   app.logger.info('Warranty eligibility check: request=%s status=%s eligible=%s',t['id'],str(status),warranty_dates.qualifies_warranty(status))
   if not warranty_dates.qualifies_warranty(status):continue
   version='030-only:'+str(row.get('modifiedAt') or sheet_data.get('version') or '')
   with engine.connect() as c:previous=c.execute(select(warranties).where(warranties.c.ticket_id==t['id'])).mappings().first()
   if previous and version and previous['checked_version']==version:
    app.logger.info('Warranty date already saved: request=%s starts=%s',t['id'],previous['started_at'])
    continue
   try:
    def get_page(page):
     response=requests.get('https://api.smartsheet.com/2.0/sheets/'+cfg[1]+'/rows/'+t['row_id']+'/columns/'+str(cfg[2]['status'])+'/history',headers={'Authorization':'Bearer '+cfg[0]},params={'page':page,'pageSize':100},timeout=5)
     response.raise_for_status();return response.json()
    history=warranty_dates.fetch_history(get_page);start=warranty_dates.closure_timestamp(history)
    if not start:
     details=[{'modifiedAt':entry.get('modifiedAt'),'statusCode':str(entry.get('value') or entry.get('displayValue') or '')[:3],'keys':sorted(entry.keys())} for entry in history.get('data',[])[:6]]
     app.logger.warning('Warranty history unresolved: request=%s count=%s total=%s entries=%s',t['id'],len(history.get('data',[])),history.get('totalCount'),json.dumps(details))
     continue
    with engine.begin() as c:
     values=dict(user_id=t['user_id'],started_at=start,checked_version=version)
     if previous:c.execute(update(warranties).where(warranties.c.ticket_id==t['id']).values(**values))
     else:c.execute(insert(warranties).values(ticket_id=t['id'],**values))
     actual=int(datetime.datetime.fromisoformat(start).timestamp())
     existing=c.execute(select(closures.c.closed_at).where(closures.c.ticket_id==t['id'])).scalar()
     if existing is None:c.execute(insert(closures).values(ticket_id=t['id'],closed_at=actual))
     else:c.execute(update(closures).where(closures.c.ticket_id==t['id']).values(closed_at=actual))
    app.logger.warning('Warranty date verified: request=%s starts=%s',t['id'],start)
   except (requests.RequestException,ValueError,KeyError) as exc:
    app.logger.warning('Exact closure time unavailable for ticket %s; error=%s http=%s',t['id'],type(exc).__name__,getattr(getattr(exc,'response',None),'status_code',None))
 finally:_warranty_lock.release()

def saved_contact(user, previous):
 contact={'name':user.get('name') or '', 'phone':'', 'address':'', 'address2':'', 'city':'', 'state':'', 'postal':''}
 if not previous:return contact
 contact.update(name=previous.get('name') or contact['name'],phone=previous.get('phone') or '')
 with engine.connect() as c:parts=c.execute(select(ticket_contacts).where(ticket_contacts.c.ticket_id==previous['id'])).mappings().first()
 if parts:contact.update(first_name=parts['first_name'],last_name=parts['last_name'])
 lines=(previous.get('address') or '').splitlines()
 if lines:
  contact['address']=lines[0]
  locality=re.fullmatch(r'(.+), ([A-Z]{2}) (\d{5}(?:-\d{4})?)',lines[-1])
  if locality:
   contact.update(city=locality[1],state=locality[2],postal=locality[3],address2='\n'.join(lines[1:-1]))
 return contact

@app.get('/api/portal')
def portal():
 u=required()
 snapshot,connected,syncing=_portal_snapshot.peek() if _portal_background else _portal_snapshot.get(load_portal_snapshot,wait_seconds=.1)
 cfg=snapshot['config'] if snapshot else None
 sheet_data=snapshot['sheet'] if snapshot else {}
 rows=sheet_data.get('rows',[])
 with engine.begin() as c:
  ts=[dict(t) for t in c.execute(select(tickets).where(tickets.c.user_id==u['id']).order_by(tickets.c.created.desc())).mappings()]
  active=[]
  for t in ts:
   t.pop('queue',None)
   saved_manager=c.execute(select(ticket_assignments).where(ticket_assignments.c.ticket_id==t['id'])).mappings().first()
   if saved_manager:
    t['manager']=saved_manager['manager'];t['managerAvailable']=True
   if t['status']=='Deleted from Smartsheet':continue
   row=next((r for r in rows if str(r['id'])==t['row_id']),None)
   if row:
    vals={str(cell['columnId']):cell.get('value') for cell in row.get('cells',[])};status=vals.get(str(cfg[2]['status']))
    manager_column=manager_column_id(sheet_data,cfg[2])
    if manager_column is not None:
     t['manager']=manager_value(row,manager_column)
     t['managerAvailable']=True
    elif not saved_manager:t['managerAvailable']=False
    if connected and status:
     t['status']=str(status)
     c.execute(update(tickets).where(tickets.c.id==t['id'],tickets.c.user_id==u['id']).values(status=t['status']))
   elif connected and t['row_id'] and snapshot['known_rows'].get(t['id'])==t['row_id']:
    # Only a successful, complete sheet read can establish that a synced row was deleted.
    t['status']='Deleted from Smartsheet'
    c.execute(update(tickets).where(tickets.c.id==t['id'],tickets.c.user_id==u['id']).values(status=t['status']))
   if t['status']=='Deleted from Smartsheet':continue
   t['deliveryStatus']='delivered' if t['row_id'] else 'pending'
   if not t['row_id']:t['status']='Saved • sending to service team'
   t['reference']=c.execute(select(request_numbers.c.reference).where(request_numbers.c.ticket_id==t['id'])).scalar()
   closed='closed' in (t['status'] or '').casefold() or bool(re.search(r'\bcomplet(?:e|ed)\b',(t['status'] or '').casefold()))
   if closed:
    closed_at=c.execute(select(closures.c.closed_at).where(closures.c.ticket_id==t['id'])).scalar()
    if closed_at is None:
     closed_at=int(time.time())
     c.execute(insert(closures).values(ticket_id=t['id'],closed_at=closed_at))
    expires=closed_at+3*24*60*60
    if time.time()>=expires:continue
    t['feedbackUntil']=datetime.datetime.fromtimestamp(expires,datetime.timezone.utc).isoformat()
   elif connected and row:
    c.execute(delete(closures).where(closures.c.ticket_id==t['id']))
   t['files']=[{'key':f['id'],'name':f['name']} for f in c.execute(select(files.c.id,files.c.name).where(files.c.ticket_id==t['id'],files.c.user_id==u['id'])).mappings()]
   active.append(t)
 with engine.connect() as c:coverage=[{'ticketId':w['ticket_id'],'reference':w['reference'],'startsAt':w['started_at'],'expiresAt':warranty_dates.anniversary(w['started_at'])} for w in c.execute(select(warranties,request_numbers.c.reference,tickets.c.status).join(tickets,warranties.c.ticket_id==tickets.c.id).outerjoin(request_numbers,warranties.c.ticket_id==request_numbers.c.ticket_id).where(warranties.c.user_id==u['id'],tickets.c.user_id==u['id'],tickets.c.status!='Deleted from Smartsheet').order_by(warranties.c.started_at.desc())).mappings() if warranty_dates.qualifies_warranty(w['status'])]
 feedback_tickets=[{'id':t['id'],'reference':t['reference'],'subject':t['subject']} for t in active if t.get('feedbackUntil')]
 return jsonify(tickets=active,feedbackTickets=feedback_tickets,warranties=coverage,contact=saved_contact(u,ts[0] if ts else None),profile={k:u[k] for k in ('plan','membership_start','membership_end','minutes_used','completion_date')},connected=connected,syncing=syncing,updatedAt=snapshot['checked_at'] if snapshot else None)

def sync_ticket(tid,include_files=True):
 # Serialize row writes/reconciliation, but never keep a new row waiting for uploads.
 with _intake_lock:delivered=_sync_ticket(tid)
 if not delivered:return False
 return sync_ticket_files(tid) if include_files else True

def deliver_intake_now(tid,wait_seconds=1):
 # The initial row write runs here, rather than relying on a request-created
 # thread or waiting for the retry sweep. All remote calls have timeouts.
 started=time.monotonic()
 if not _intake_delivery_slots.acquire(blocking=False):
  _intake_wake.set();return False
 try:
  if not _intake_lock.acquire(timeout=.1):
   app.logger.warning('Immediate intake queued: writer busy request=%s',tid)
   return False
  try:delivered=_sync_ticket(tid)
  finally:_intake_lock.release()
  app.logger.warning('Immediate intake result: request=%s delivered=%s elapsed=%.2fs',tid,delivered,time.monotonic()-started)
  return delivered
 except Exception as exc:
  app.logger.warning('Immediate intake deferred: request=%s error=%s',tid,type(exc).__name__)
  return False
 finally:
  _intake_delivery_slots.release();_intake_wake.set()

def resolve_ticket_client(t, submitted_job='', client_sheet=None):
 if client_sheet is None:return None
 return customer_database.intake_lookup(client_sheet,email=t['email'],name=t['name'],telephone=t['phone'],address=t['address'],job_number=submitted_job)

def assign_ticket_number(t,resolution=None):
 with engine.begin() as c:
  user=c.execute(select(users).where(users.c.id==t['user_id'])).mappings().first()
  return ticket_numbers.assign(c,number_tables,t,user,resolution)

def intake_values(ticket, submitted_job='', client_sheet=None, reference=None, resolution=None):
 t=dict(ticket);matched=None;related_jobs=[]
 if resolution is None:resolution=resolve_ticket_client(t,submitted_job,client_sheet)
 if resolution is not None:
  matched=resolution['record'];related_jobs=resolution['job_numbers']
 name=(matched or {}).get('name') or t['name']
 parts=name.strip().split(maxsplit=1)
 submitted=datetime.datetime.fromisoformat(t['created'].replace('Z','+00:00')).astimezone(ZoneInfo('America/Los_Angeles'))
 timestamp=submitted.strftime('%m/%d/%Y %I:%M:%S %p %Z')
 return {**t,'job_number':' / '.join(related_jobs) if related_jobs else submitted_job or None,
  'phone':customer_database.format_phone(t['phone']),
  'ticket':reference or 'Number pending','status':'000 - Intake',
  'first_name':t.get('_first_name') or (matched or {}).get('first_name') or (parts[0] if parts else ''),
  'last_name':t.get('_last_name') or (matched or {}).get('last_name') or ('' if '_last_name' in t else (parts[1] if len(parts)>1 else '')),
  'date_realized':submitted.date().isoformat(),'submitted_at':timestamp,
  'description':t['subject']+'\n\n'+t['description']+'\n\nSystems: '+t['category']+'\nSubmitted by: '+t['name']+'\nSubmitted at: '+timestamp+'\nWebsite request ID: '+t['id']+('\n\nClient database job references: '+', '.join(related_jobs) if related_jobs else '')}

def _sync_ticket(tid):
 started=time.monotonic()
 cfg=smartsheet_config()
 if not cfg:return False
 with engine.connect() as c:
  t=dict(c.execute(select(tickets).where(tickets.c.id==tid)).mappings().one())
  parts=c.execute(select(ticket_contacts).where(ticket_contacts.c.ticket_id==tid)).mappings().first()
  if parts:t.update(_first_name=parts['first_name'],_last_name=parts['last_name'])
  job_number=c.execute(select(ticket_details.c.job_number).where(ticket_details.c.ticket_id==tid)).scalar()
  reference=c.execute(select(request_numbers.c.reference).where(request_numbers.c.ticket_id==tid)).scalar()
 resolution=None
 if not t['row_id']:
  try:resolution=resolve_ticket_client(t,job_number,customer_database.get_sheet())
  except (requests.RequestException,RuntimeError,ValueError,KeyError):
   app.logger.warning('Client database matching unavailable; submitted details retained')
  if not reference:reference=assign_ticket_number(t,resolution)
 cfg=smartsheet_config()
 if not cfg:return False
 row=t['row_id']
 try:
  if not row:
   # A brand-new, durably recorded request has never been sent. Only retries
   # need the full-sheet duplicate check after an uncertain write/crash.
   with engine.begin() as c:
    attempted=c.execute(select(intake_attempts.c.attempted).where(intake_attempts.c.ticket_id==tid)).scalar()
    if attempted is not None:c.execute(update(intake_attempts).where(intake_attempts.c.ticket_id==tid).values(attempted=1))
   matches=[]
   if attempted!=0:
    r=smart_request('GET','',params={'includeAll':'true'},timeout=(2,4));r.raise_for_status()
    data=r.json()
    if not isinstance(data.get('rows'),list) or len(data['rows'])<data.get('totalRowCount',len(data['rows'])):return False
    matches=[r for r in data['rows'] if ticket_numbers.matches_request(r,cfg[2],tid)]
   if len(matches)>1:return False
   if matches:row=str(matches[0]['id'])
   else:
    vals=intake_values(t,job_number,reference=reference,resolution=resolution)
    cells=[{'columnId':int(cid),'value':vals[k]} for k,cid in cfg[2].items() if k in vals and vals[k] is not None and k!='queue']
    r=smart_request('POST','/rows',json=[{'toBottom':True,'cells':cells}],timeout=(2,4));r.raise_for_status();row=str(r.json()['result'][0]['id'])
   with engine.begin() as c:
    c.execute(update(tickets).where(tickets.c.id==tid).values(row_id=row,status='000 - Intake'))
    # Reconciled rows already carry the saved reference when they came from this writer.
    if not matches and reference:c.execute(update(request_numbers).where(request_numbers.c.ticket_id==tid).values(synced=1))
   app.logger.warning('Smartsheet delivery confirmed for ticket %s in %.2fs',tid,time.monotonic()-started)
  return True
 except (requests.RequestException,ValueError,KeyError,IndexError) as exc:
  response=getattr(exc,'response',None)
  app.logger.warning('Smartsheet delivery pending for ticket %s; HTTP status %s',tid,getattr(response,'status_code','unavailable'))
  return False

def refresh_ticket_numbers():
 # Backfill existing requests, including completed warranty records, oldest first.
 # Only the website-number column is updated; intake dates/statuses stay intact.
 cfg=smartsheet_config()
 if not cfg:return
 with engine.connect() as c:
  pending=list(c.execute(select(tickets,ticket_details.c.job_number,request_numbers.c.reference,request_numbers.c.synced)
   .outerjoin(ticket_details,tickets.c.id==ticket_details.c.ticket_id)
   .outerjoin(request_numbers,tickets.c.id==request_numbers.c.ticket_id)
   .where((request_numbers.c.ticket_id.is_(None)) | (request_numbers.c.synced==0))
   .order_by(tickets.c.created,tickets.c.id)).mappings())
 sheet=None
 for item in pending:
  t=dict(item)
  try:
   reference=t['reference']
   if not reference:
    if sheet is None:sheet=customer_database.get_sheet()
    reference=assign_ticket_number(t,resolve_ticket_client(t,t['job_number'],sheet))
   with _intake_lock:
    if not reference or not t['row_id'] or t['status']=='Deleted from Smartsheet':continue
    r=smart_request('PUT','/rows',json=[{'id':int(t['row_id']),'cells':[{'columnId':int(cfg[2]['ticket']),'value':reference}]}])
    # A deleted intake row must never be re-created by a numbering migration.
    if r.status_code!=404:r.raise_for_status()
    with engine.begin() as c:c.execute(update(request_numbers).where(request_numbers.c.ticket_id==t['id']).values(synced=1))
    app.logger.warning('Customer ticket number synchronized for request %s',t['id'])
  except (requests.RequestException,RuntimeError,ValueError,KeyError):
   app.logger.warning('Customer ticket number retry pending for request %s',t['id'])
   if sheet is None:break  # Do not repeat an unavailable client-sheet read for every row.

def sync_ticket_files(tid):
 # Separate upload locking preserves duplicate protection without blocking row creation.
 with _intake_attachment_lock:
  try:
   with engine.connect() as c:
    row=c.execute(select(tickets.c.row_id).where(tickets.c.id==tid)).scalar()
    fs=list(c.execute(select(files).where(files.c.ticket_id==tid,files.c.synced==0)).mappings())
   if not row:return False
   for f in fs:
    r=smart_request('POST','/rows/'+row+'/attachments',data=f['data'],headers={'Content-Type':f['type'],'Content-Disposition':'attachment; filename="'+re.sub('[^a-zA-Z0-9._-]','_',f['name'])+'"'});r.raise_for_status()
    with engine.begin() as c:c.execute(update(files).where(files.c.id==f['id']).values(synced=1))
   return True
  except (requests.RequestException,ValueError,KeyError,IndexError) as exc:
   app.logger.warning('Smartsheet attachment delivery pending for ticket %s; HTTP status %s',tid,getattr(getattr(exc,'response',None),'status_code','unavailable'))
   # Attachment retries must not overwrite completed/deleted status.
   return False
@app.get('/qa/background')
def qa_background():
 return send_file(io.BytesIO(base64.b64decode((Path(__file__).parent/'pdf-data/qa-background.b64').read_text())),mimetype='image/jpeg')
@app.get('/qa/template')
def qa_template():
 u=required()
 with engine.connect() as c:
  t=c.execute(select(tickets).where(tickets.c.id==request.args.get('ticketId'),tickets.c.user_id==u['id'])).first()
  if not t:abort(404)
  closed_at=c.execute(select(closures.c.closed_at).where(closures.c.ticket_id==t.id)).scalar()
  if not warranty_dates.is_closed(t.status) or closed_at is None or time.time()>=closed_at+259200:abort(403)
 return send_file(io.BytesIO(qa_pdf.template()),mimetype='application/pdf',download_name='Quality-Assurance-Form.pdf')
@app.post('/api/portal')
def save_ticket():
 u=required();rate('ticket:'+u['id'],15);f=request.form
 if f.get('action')=='review':
  with engine.begin() as c:
   t=c.execute(select(tickets).where(tickets.c.id==f.get('ticketId'),tickets.c.user_id==u['id'])).first()
   if not t:abort(400,description='Select one of your tickets.')
   closed_at=c.execute(select(closures.c.closed_at).where(closures.c.ticket_id==t.id)).scalar()
   status=(t.status or '').casefold()
   if not ('closed' in status or re.search(r'\bcomplet(?:e|ed)\b',status)) or closed_at is None or time.time()>=closed_at+259200:
    abort(400,description='The QA form is available only for closed tickets within three days of closure.')
   if any(not f.get(k,'').strip() for k in ('serviceDate','customerName','signatureDate')) or any(f.get(k) not in ('Exceeds','Meets','Could do better') for k in ('Service technician','Service department')):
    abort(400,description='Complete the service date, both ratings, client signature, and signature date.')
   for k in ('serviceDate','signatureDate'):
    try:datetime.datetime.strptime(f[k],'%m/%d/%Y') if '/' in f[k] else datetime.date.fromisoformat(f[k])
    except ValueError:abort(400,description='Enter dates as MM/DD/YYYY.')
   data=json.dumps(f.to_dict())
   if len(data)>10000:abort(400,description='Please shorten your notes.')
   review_id=secrets.token_hex(16)
   c.execute(insert(reviews).values(id=review_id,user_id=u['id'],ticket_id=t.id,data=data,created=now()))
   customer_name=' '.join(f['customerName'].split())[:100]
   reference=c.execute(select(request_numbers.c.reference).where(request_numbers.c.ticket_id==t.id)).scalar()
   values=f.to_dict();values['Job number']=values.get('Job number') or reference or ''
   pdf=qa_pdf.completed(values)
   file_reference=re.sub(r'[^A-Za-z0-9_-]','-',reference) if reference else 'Service-request'
   payload=json.dumps({'kind':'qa_pdf','pdf':base64.b64encode(pdf).decode(),'filename':'Customer-feedback-'+file_reference+'.pdf'})
   notifications.enqueue(c,email_outbox,'feedback-'+review_id,'Customer feedback - '+customer_name+' - '+(reference or 'Service request'),payload,u['email'])
  if notifications.configured():
   threading.Thread(target=notifications.deliver_pending,args=(engine,email_outbox,app.logger),daemon=True).start()
   return {'message':'Your completed QA form has been saved and queued for email delivery to Alec and Customer Service.','emailStatus':'queued'}
  return {'message':'Your completed QA form has been saved. Email delivery is pending because the service team’s email connection is not yet enabled.','emailStatus':'not_configured'}
 fields=('name','phone','address','category','subject','description');d={k:f.get(k,'').strip() for k in fields}
 first=f.get('first_name','').strip();last=f.get('last_name','').strip()
 if 'first_name' in f:
  if not first or len(first)>150 or len(last)>150:abort(400,description='Enter your first name and check the name lengths.')
  d['name']=' '.join(x for x in (first,last) if x)
 city=f.get('city','').strip();state=f.get('state','').strip().upper();postal=f.get('postal','').strip();address2=f.get('address2','').strip()
 if not d['address'] or len(d['address'])>150 or not city or len(city)>100 or not re.fullmatch(r'[A-Z]{2}',state) or not re.fullmatch(r'\d{5}(?:-\d{4})?',postal) or len(address2)>150:abort(400,description='Enter the full address, including city, two-letter state, and ZIP code.')
 d['address']='\n'.join(x for x in (d['address'],address2,city+', '+state+' '+postal) if x)
 categories=list(dict.fromkeys(x.strip() for x in f.getlist('category') if x.strip()))
 if not categories or any(x not in ('Solar','Battery','Generac','Monitoring','Warranty','Other') for x in categories):abort(400,description='Select at least one valid system or service type.')
 d['category']=', '.join(categories)
 if any(not d[k] or len(d[k])>(5000 if k=='description' else 300) for k in fields):abort(400,description='Please complete all fields and keep the description under 5,000 characters.')
 d['phone']=customer_database.format_phone(d['phone'])
 if f.get('ack')!='on':abort(400,description='Please acknowledge the service rates and policies.')
 uploads=[x for x in request.files.getlist('photos') if x.filename];stored=[];total=0
 if len(uploads)>5:abort(400,description='Attach up to 5 files.')
 for x in uploads:
  blob=x.read();total+=len(blob)
  valid=(Path(x.filename).suffix.lower() in ('.jpg','.jpeg') and x.mimetype=='image/jpeg' and blob.startswith(b'\xff\xd8\xff')) or (Path(x.filename).suffix.lower()=='.pdf' and x.mimetype=='application/pdf' and blob.startswith(b'%PDF-'))
  if total>20*1024*1024 or not valid:abort(400,description='Photos must be JPEG/JPG. Documents must be PDF. Attach up to 5 files totaling at most 20 MB.')
  stored.append(dict(id=secrets.token_hex(16),user_id=u['id'],name=x.filename[:200],type=x.mimetype,data=blob,synced=0))
 request_key=f.get('request_key','').strip()
 if request_key and not re.fullmatch(r'[A-Za-z0-9-]{20,80}',request_key):abort(400,description='Please reopen the request form and try again.')
 tid='HOS-'+(hashlib.sha256((u['id']+':'+request_key).encode()).hexdigest()[:32].upper() if request_key else secrets.token_hex(6).upper())
 created_at=now()
 try:
  with engine.begin() as c:
   c.execute(insert(tickets).values(id=tid,user_id=u['id'],email=u['email'],created=created_at,status='Saved • sending to service team',**d))
   c.execute(insert(intake_attempts).values(ticket_id=tid,attempted=0))
   c.execute(insert(ticket_details).values(ticket_id=tid,job_number=f.get('job_number','').strip()[:100]))
   if 'first_name' in f:c.execute(insert(ticket_contacts).values(ticket_id=tid,first_name=first,last_name=last))
   for x in stored:c.execute(insert(files).values(ticket_id=tid,**x))
 except IntegrityError:
  with engine.connect() as c:existing=c.execute(select(tickets.c.id).where(tickets.c.id==tid,tickets.c.user_id==u['id'])).scalar()
  if not request_key or not existing:raise
 deliver_intake_now(tid)
 return submission_receipt(tid,u['id'])

def submission_receipt(tid,user_id):
 with engine.connect() as c:
  t=dict(c.execute(select(tickets).where(tickets.c.id==tid,tickets.c.user_id==user_id)).mappings().one())
  t['reference']=c.execute(select(request_numbers.c.reference).where(request_numbers.c.ticket_id==tid)).scalar()
  t['files']=[{'key':x['id'],'name':x['name']} for x in c.execute(select(files.c.id,files.c.name).where(files.c.ticket_id==tid,files.c.user_id==user_id)).mappings()]
 delivered=bool(t.pop('row_id',None));t.pop('queue',None)
 t['deliveryStatus']='delivered' if delivered else 'pending'
 if not delivered:t['status']='Saved • sending to service team'
 message='Your request has been received by our service team.' if delivered else 'Your request is safely saved. We are confirming delivery to our service team and will retry automatically. Please do not submit a second request.'
 return {'id':tid,'deliveryStatus':t['deliveryStatus'],'ticket':t,'message':message}
@app.get('/api/file')
def file_download():
 u=required()
 with engine.connect() as c:f=c.execute(select(files).where(files.c.id==request.args.get('key'),files.c.user_id==u['id'])).mappings().first()
 if not f:abort(404)
 return send_file(io.BytesIO(f['data']),mimetype=f['type'],download_name=f['name'],as_attachment=True)
@app.route('/staff',methods=['GET','POST'])
def staff_page():
 staff()
 if request.method=='POST':
  if request.form.get('action')=='sync':sync_ticket(request.form.get('ticket_id',''))
  else:
   uid=request.form.get('user_id');plan=request.form.get('plan','None');minutes=int(request.form.get('minutes_used','0'))
   if plan not in ('None','Basic','Gold') or not 0<=minutes<=30 or minutes%15:abort(400,description='Choose a valid plan and support usage of 0, 15 or 30 minutes.')
   dates={k:request.form.get(k) or None for k in ('membership_start','membership_end','completion_date')}
   for v in dates.values():
    if v:
     try:datetime.date.fromisoformat(v)
     except ValueError:abort(400,description='Invalid date.')
   if dates['membership_start'] and dates['membership_end'] and dates['membership_end']<dates['membership_start']:abort(400,description='Membership end must follow its start.')
   with engine.begin() as c:c.execute(update(users).where(users.c.id==uid).values(plan=plan,minutes_used=minutes,**dates))
  return redirect('/staff')
 with engine.connect() as c:
  us=list(c.execute(select(users.c.id,users.c.email,users.c.name,users.c.plan,users.c.membership_start,users.c.membership_end,users.c.minutes_used,users.c.completion_date)).mappings());ts=list(c.execute(select(tickets,request_numbers.c.reference).outerjoin(request_numbers,tickets.c.id==request_numbers.c.ticket_id).order_by(tickets.c.created.desc()).limit(100)).mappings());rs=list(c.execute(select(reviews,request_numbers.c.reference).outerjoin(request_numbers,reviews.c.ticket_id==request_numbers.c.ticket_id).order_by(reviews.c.created.desc()).limit(100)).mappings())
 token=request.cookies.get('care_csrf') or secrets.token_urlsafe(32);r=make_response(render_template('staff.html',users=us,tickets=ts,reviews=rs,csrf=token,connected=bool(smartsheet_config())));r.set_cookie('care_csrf',token,httponly=True,secure=bool(os.getenv('RENDER')),samesite='Lax');return r
@app.cli.command('create-admin')
def create_admin():
 import click
 db_required();email=click.prompt('Verified staff email').strip().lower();password=click.prompt('New staff password',hide_input=True,confirmation_prompt=True)
 if len(password)<12:raise click.ClickException('Use at least 12 characters.')
 with engine.begin() as c:
  u=c.execute(select(users.c.id).where(users.c.email==email)).first()
  if u:c.execute(update(users).where(users.c.id==u.id).values(admin=1,password=generate_password_hash(password)));c.execute(delete(sessions).where(sessions.c.user_id==u.id))
  else:c.execute(insert(users).values(id=secrets.token_hex(16),email=email,name='Service team',password=generate_password_hash(password),admin=1,plan='None',minutes_used=0))
 click.echo('Staff account configured.')
_column_migration_done=False
_column_migration_retry=0
def migrate_intake_columns():
 global _column_migration_done,_column_migration_retry
 if _column_migration_done or time.time()<_column_migration_retry:return
 _column_migration_retry=time.time()+300
 cfg=smartsheet_config()
 if not cfg:return
 with _intake_lock:
  def api(method,path,**kwargs):
   response=smart_request(method,path,**kwargs);response.raise_for_status();return response.json()
  def backup(payload):
   content=json.dumps(payload,sort_keys=True)
   key=hashlib.sha256((cfg[1]+content).encode()).hexdigest()
   with engine.begin() as c:
    if not c.execute(select(column_backups.c.id).where(column_backups.c.id==key)).scalar():
     c.execute(insert(column_backups).values(id=key,created=now(),payload=content))
  try:
   result=intake_columns.migrate(api,backup,lambda count:app.logger.warning('Intake column copy verified: updated_rows=%s; legacy values backed up',count))
   _column_migration_done=True
   app.logger.warning('Intake column migration complete: moved=%s removed=%s',result['moved'],result['removed'])
  except requests.RequestException as exc:
   status=getattr(getattr(exc,'response',None),'status_code',None)
   if status==403:_column_migration_retry=float('inf')
   app.logger.warning('Intake column migration pending: HTTP %s',status)
  except (ValueError,KeyError):app.logger.warning('Intake column migration pending: verification incomplete; old columns retained')

def refresh_portal_loop():
 last_states={}
 # Independent of uploads, client matching, migrations and warranty history.
 while True:
  started=time.monotonic()
  try:
   snapshot=load_portal_snapshot()
   _portal_snapshot.publish(snapshot)
   mapped=snapshot['config'][2]
   linked=set(str(v) for v in snapshot['known_rows'].values())
   for row in snapshot['sheet']['rows']:
    if str(row['id']) not in linked:continue
    cells={str(cell['columnId']):cell for cell in row.get('cells',[])}
    status=cells.get(str(mapped['status']),{}).get('value')
    manager=cells.get(str(mapped.get('manager')), {})
    state=(status,json.dumps(manager,sort_keys=True))
    if last_states.get(str(row['id']))!=state:
     app.logger.warning('Portal row refreshed: row=%s status=%s manager_present=%s elapsed=%.2fs',row['id'],status,bool(manager.get('value') or manager.get('displayValue') or manager.get('objectValue')),time.monotonic()-started)
     last_states[str(row['id'])]=state
  except Exception:
   _portal_snapshot.failed()
  threading.Event().wait(max(.2,2-(time.monotonic()-started)))

def refresh_warranty_loop():
 while True:
  try:
   snapshot,connected,_=_portal_snapshot.peek()
   if snapshot and connected:
    with engine.connect() as c:owned=list(c.execute(select(tickets).where(tickets.c.row_id.is_not(None),tickets.c.status!='Deleted from Smartsheet')).mappings())
    refresh_warranties(snapshot['sheet'],snapshot['config'],owned)
  except Exception as exc:app.logger.warning('Warranty refresh deferred: %s',type(exc).__name__)
  threading.Event().wait(1)

def warm_client_cache():
 while True:
  try:customer_database.get_sheet(force_refresh=True)
  except Exception:app.logger.warning('Client cache refresh deferred')
  threading.Event().wait(45)

_intake_wake=threading.Event()
def retry_pending_intake():
 while True:
  try:
   if engine and smartsheet_config():
    with engine.connect() as c:
     pending=set(c.execute(select(tickets.c.id).where(tickets.c.row_id.is_(None))).scalars())
     pending.update(c.execute(select(files.c.ticket_id).where(files.c.synced==0)).scalars())
    for tid in sorted(pending):
     try:sync_ticket(tid,include_files=False)
     except Exception:app.logger.warning('Intake retry deferred for request %s',tid)
    for tid in sorted(pending):
     try:sync_ticket_files(tid)
     except Exception:app.logger.warning('Attachment retry deferred for request %s',tid)
    try:refresh_ticket_numbers()
    except Exception:app.logger.warning('Customer ticket numbering deferred; requests remain saved')
    migrate_intake_columns()
  except Exception:
   app.logger.warning('Intake retry deferred; requests remain saved')
  _intake_wake.wait(2)
  _intake_wake.clear()
def check_client_database():
 try:
  sheet=customer_database.get_sheet()
  app.logger.warning('Client intake database accessible; columns: %s', ', '.join(col['title'] for col in sheet.get('columns',[])))
  if engine:
   # Read-only verification against recent submissions. Log only presence/counts,
   # never client names, contact details or the matched job numbers.
   with engine.connect() as c:
    recent=list(c.execute(select(tickets.c.id,tickets.c.name,tickets.c.email,tickets.c.phone,tickets.c.address,ticket_details.c.job_number).outerjoin(ticket_details,tickets.c.id==ticket_details.c.ticket_id).order_by(tickets.c.created.desc()).limit(1)).mappings())
   for t in recent:
    result=customer_database.intake_lookup(sheet,email=t['email'],name=t['name'],telephone=t['phone'],address=t['address'],job_number=t['job_number'])
    app.logger.warning('Intake lookup check for %s: matched=%s, last_name=%s, job_count=%s',t['id'],bool(result['record']),bool((result['record'] or {}).get('last_name')),len(result['job_numbers']))
 except (requests.RequestException,RuntimeError,ValueError,KeyError):
  app.logger.warning('Client intake database unavailable: API account must have access to the configured client sheet')
 except SQLAlchemyError:
   app.logger.warning('Recent intake lookup check deferred: account storage unavailable')
if os.getenv('RENDER') and os.getenv('SMARTSHEET_TOKEN'):
 _portal_background=True
 threading.Thread(target=refresh_portal_loop,name='portal-status-sync',daemon=True).start()
 threading.Thread(target=refresh_warranty_loop,name='warranty-sync',daemon=True).start()
 threading.Thread(target=warm_client_cache,name='client-cache',daemon=True).start()
 threading.Thread(target=check_client_database,name='client-database-check',daemon=True).start()
 threading.Thread(target=retry_pending_intake,name='intake-delivery',daemon=True).start()
if os.getenv('RENDER') and notifications.configured():
 threading.Thread(target=notifications.check_account_sender,args=(app.logger,),name='account-email-check',daemon=True).start()
 threading.Thread(target=notifications.run_worker,args=(engine,email_outbox,app.logger),name='staff-email',daemon=True).start()
account_security.install(sys.modules[__name__])
if __name__=='__main__':app.run(host='0.0.0.0',port=int(os.getenv('PORT','5000')))
