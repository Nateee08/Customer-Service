import os,json,base64,secrets,hashlib,time,datetime,smtplib,io,re,threading
from zoneinfo import ZoneInfo
from email.message import EmailMessage
from urllib.parse import urlsplit
from flask import Flask,request,jsonify,render_template,redirect,make_response,send_file,abort
from werkzeug.security import generate_password_hash,check_password_hash
from sqlalchemy import create_engine,MetaData,Table,Column,String,Integer,Text,LargeBinary,select,insert,update,delete,and_,func
from sqlalchemy.exc import IntegrityError
import requests
import notifications
app=Flask(__name__)
app.config['MAX_CONTENT_LENGTH']=22*1024*1024
meta=MetaData()
email_outbox=notifications.define_table(meta)
users=Table('care_users',meta,Column('id',String,primary_key=True),Column('email',String,unique=True,nullable=False),Column('name',String,nullable=False),Column('password',Text,nullable=False),Column('admin',Integer,nullable=False,default=0),Column('plan',String,nullable=False,default='None'),Column('membership_start',String),Column('membership_end',String),Column('minutes_used',Integer,nullable=False,default=0),Column('completion_date',String))
sessions=Table('care_sessions',meta,Column('token',String,primary_key=True),Column('user_id',String,nullable=False),Column('expires',Integer,nullable=False))
tickets=Table('care_tickets',meta,Column('id',String,primary_key=True),Column('user_id',String,nullable=False),Column('email',String,nullable=False),Column('name',String),Column('phone',String),Column('address',String),Column('category',String),Column('subject',String),Column('description',Text),Column('created',String),Column('status',String),Column('row_id',String),Column('queue',Integer))
files=Table('care_files',meta,Column('id',String,primary_key=True),Column('ticket_id',String,nullable=False),Column('user_id',String,nullable=False),Column('name',String),Column('type',String),Column('data',LargeBinary),Column('synced',Integer,default=0))
reviews=Table('care_reviews',meta,Column('id',String,primary_key=True),Column('ticket_id',String),Column('user_id',String),Column('data',Text),Column('created',String))
resets=Table('care_resets',meta,Column('token',String,primary_key=True),Column('user_id',String),Column('expires',Integer))
limits=Table('care_limits',meta,Column('key',String,primary_key=True),Column('count',Integer,nullable=False))
url=os.getenv('DATABASE_URL','')
if url.startswith('postgres://'):url=url.replace('postgres://','postgresql+psycopg://',1)
elif url.startswith('postgresql://'):url=url.replace('postgresql://','postgresql+psycopg://',1)
if not url and os.getenv('DATA_DIR'):
 os.makedirs(os.environ['DATA_DIR'],exist_ok=True);url='sqlite:///'+os.path.join(os.environ['DATA_DIR'],'customer-care.db')
engine=create_engine(url,pool_pre_ping=True) if url else None
if engine:meta.create_all(engine)
DOCS=json.load(open(os.path.join(os.path.dirname(__file__),'documents.json')))
def digest(s):return hashlib.sha256(s.encode()).hexdigest()
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
 if request.path.startswith(('/api','/login','/register','/staff','/forgot','/reset')):r.headers['Cache-Control']='no-store'
 return r
@app.errorhandler(413)
def too_large(e):return jsonify(error='Attachments must total less than 20 MB.'),413
@app.errorhandler(401)
@app.errorhandler(403)
@app.errorhandler(400)
@app.errorhandler(503)
def problem(e):
 if request.path.startswith('/api/'):return jsonify(error=e.description),e.code
 return render_template('auth.html',title='Customer care',message=e.description,enabled=False,mode='login'),e.code
@app.get('/')
def home():return render_template('index.html')
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
 db_required();email=request.form.get('email','').strip().lower();password=request.form.get('password','');rate('auth:'+email)
 if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email) or len(email)>254 or not 12<=len(password)<=128:return auth_view(mode,'Use a valid email and a password of 12–128 characters.',400)
 with engine.begin() as c:
  u=c.execute(select(users).where(users.c.email==email)).mappings().first()
  if mode=='register':
   name=request.form.get('name','').strip()
   if not name or len(name)>200:return auth_view(mode,'Please enter your name.',400)
   if u:return auth_view(mode,'Unable to create this account. Try signing in or resetting your password.',400)
   uid=secrets.token_hex(16)
   try:c.execute(insert(users).values(id=uid,email=email,name=name,password=generate_password_hash(password),admin=0,plan='None',minutes_used=0))
   except IntegrityError:return auth_view(mode,'Unable to create this account.',400)
  else:
   if not u or not check_password_hash(u['password'],password):return auth_view(mode,'Email or password is incorrect.',400)
   uid=u['id']
  token=secrets.token_urlsafe(32);c.execute(insert(sessions).values(token=digest(token),user_id=uid,expires=int(time.time())+86400*7))
 r=redirect('/');r.set_cookie('care_session',token,httponly=True,secure=bool(os.getenv('RENDER')),samesite='Lax',max_age=86400*7);return r
@app.get('/logout')
def logout():
 if engine:
  with engine.begin() as c:c.execute(delete(sessions).where(sessions.c.token==digest(request.cookies.get('care_session',''))))
 r=redirect('/');r.delete_cookie('care_session');return r
@app.route('/forgot-password',methods=['GET','POST'])
def forgot():
 if request.method=='GET':return auth_view('forgot')
 db_required()
 if not os.getenv('SMTP_HOST') or not os.getenv('PUBLIC_URL'):return auth_view('forgot','Password reset email is not configured yet. Please contact customer service.',503)
 email=request.form.get('email','').strip().lower();rate('reset:'+email,3)
 with engine.begin() as c:
  u=c.execute(select(users).where(users.c.email==email)).mappings().first()
  if u:
   token=secrets.token_urlsafe(32);c.execute(insert(resets).values(token=digest(token),user_id=u['id'],expires=int(time.time())+1800))
   msg=EmailMessage();msg['Subject']='Reset your Hooked on Solar password';msg['From']=os.getenv('SMTP_FROM',os.getenv('SMTP_USER'));msg['To']=email;msg.set_content('Use this link within 30 minutes to reset your password:\n'+os.environ['PUBLIC_URL'].rstrip('/')+'/reset-password/'+token)
   try:
    with smtplib.SMTP(os.environ['SMTP_HOST'],int(os.getenv('SMTP_PORT','587')),timeout=15) as smtp:
     smtp.starttls();smtp.login(os.environ['SMTP_USER'],os.environ['SMTP_PASSWORD']);smtp.send_message(msg)
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
  c.execute(update(users).where(users.c.id==row['user_id']).values(password=generate_password_hash(password)));c.execute(delete(resets).where(resets.c.user_id==row['user_id']));c.execute(delete(sessions).where(sessions.c.user_id==row['user_id']))
 return redirect('/login')
_intake_lock=threading.RLock()
_intake_columns=None
_intake_retry_at=0
def smartsheet_config():
 global _intake_columns,_intake_retry_at
 token=os.getenv('SMARTSHEET_TOKEN');sheet=os.getenv('SMARTSHEET_SHEET_ID')
 if not token or not sheet:return None
 with _intake_lock:
  if _intake_columns:return token,sheet,_intake_columns
  if time.time()<_intake_retry_at:return None
  try:
   explicit=json.loads(os.getenv('SMARTSHEET_COLUMNS','{}'))
   if explicit and all(explicit.get(k) for k in ('ticket','email','subject','description','status')):
    _intake_columns=explicit;return token,sheet,explicit
   base='https://api.smartsheet.com/2.0/sheets/'+sheet
   headers={'Authorization':'Bearer '+token}
   r=requests.get(base+'/columns',headers=headers,timeout=15);r.raise_for_status()
   columns=r.json()['data'];by_title={c['title'].strip().casefold():c for c in columns}
   mapping={'first_name':'First Name','last_name':'Last Name','email':'Email','phone':'Phone #','address':'Address','description':'Notes:','status':'Status','date_realized':'Date Realized'}
   missing=[title for title in mapping.values() if title.casefold() not in by_title]
   if missing:raise ValueError('Required intake columns missing: '+', '.join(missing))
   for key,title in [('ticket','Website Ticket #'),('subject','Website Subject')]:
    if title.casefold() not in by_title:
     r=requests.post(base+'/columns',headers=headers,json=[{'title':title,'type':'TEXT_NUMBER','index':len(columns)}],timeout=15);r.raise_for_status()
     added=r.json()['result'];columns.extend(added)
     by_title.update({c['title'].strip().casefold():c for c in added})
    mapping[key]=title
   _intake_columns={key:by_title[title.casefold()]['id'] for key,title in mapping.items()}
   app.logger.warning('Smartsheet intake column mapping ready')
   return token,sheet,_intake_columns
  except (requests.RequestException,ValueError,KeyError):
   _intake_retry_at=time.time()+60
   app.logger.warning('Smartsheet intake setup failed; will retry. Check API access and required column titles.')
   return None

def smart_request(method,path,**kwargs):
 cfg=smartsheet_config();return requests.request(method,'https://api.smartsheet.com/2.0/sheets/'+cfg[1]+path,headers={'Authorization':'Bearer '+cfg[0],**kwargs.pop('headers',{})},timeout=15,**kwargs)
@app.get('/api/portal')
def portal():
 u=required();cfg=smartsheet_config();rows=[];connected=False
 if cfg:
  try:
   r=smart_request('GET','');r.raise_for_status();rows=r.json().get('rows',[]);connected=True
  except requests.RequestException:pass
 with engine.begin() as c:
  ts=[dict(t) for t in c.execute(select(tickets).where(tickets.c.user_id==u['id']).order_by(tickets.c.created.desc())).mappings()]
  feedback_tickets=[{'id':t['id'],'subject':t['subject']} for t in ts]
  active=[]
  for t in ts:
   t.pop('queue',None)
   row=next((r for r in rows if str(r['id'])==t['row_id']),None)
   if row:
    vals={str(cell['columnId']):cell.get('value') for cell in row.get('cells',[])};status=vals.get(str(cfg[2]['status']))
    if status:
     t['status']=str(status)
     c.execute(update(tickets).where(tickets.c.id==t['id'],tickets.c.user_id==u['id']).values(status=t['status']))
   if 'closed' in (t['status'] or '').casefold():continue
   t['files']=[{'key':f['id'],'name':f['name']} for f in c.execute(select(files.c.id,files.c.name).where(files.c.ticket_id==t['id'],files.c.user_id==u['id'])).mappings()]
   active.append(t)
 return jsonify(tickets=active,feedbackTickets=feedback_tickets,profile={k:u[k] for k in ('plan','membership_start','membership_end','minutes_used','completion_date')},connected=connected)

def sync_ticket(tid):
 with _intake_lock:return _sync_ticket(tid)
def _sync_ticket(tid):
 cfg=smartsheet_config()
 if not cfg:return False
 with engine.connect() as c:t=dict(c.execute(select(tickets).where(tickets.c.id==tid)).mappings().one())
 row=t['row_id']
 try:
  if not row:
   # Reconcile by unique ticket number before retrying a possibly accepted request.
   r=smart_request('GET','');r.raise_for_status();matches=[r for r in r.json().get('rows',[]) if any(str(c['columnId'])==str(cfg[2]['ticket']) and c.get('value')==tid for c in r.get('cells',[]))]
   if len(matches)>1:return False
   if matches:row=str(matches[0]['id'])
   else:
    parts=t['name'].strip().split(maxsplit=1)
    vals={**t,'ticket':tid,'status':'000 - Intake','first_name':parts[0],'last_name':parts[1] if len(parts)>1 else '', 'date_realized':datetime.datetime.fromisoformat(t['created']).astimezone(ZoneInfo('America/Los_Angeles')).date().isoformat(), 'description':t['subject']+'\n\n'+t['description']+'\n\nSystem: '+t['category']+'\nSubmitted by: '+t['name']+'\nWebsite ticket: '+tid};cells=[{'columnId':int(cid),'value':vals[k]} for k,cid in cfg[2].items() if k in vals and vals[k] is not None and k!='queue']
    r=smart_request('POST','/rows',json=[{'toBottom':True,'cells':cells}]);r.raise_for_status();row=str(r.json()['result'][0]['id'])
   with engine.begin() as c:c.execute(update(tickets).where(tickets.c.id==tid).values(row_id=row,status='000 - Intake'))
  with engine.connect() as c:fs=list(c.execute(select(files).where(files.c.ticket_id==tid,files.c.synced==0)).mappings())
  for f in fs:
   r=smart_request('POST','/rows/'+row+'/attachments',data=f['data'],headers={'Content-Type':f['type'],'Content-Disposition':'attachment; filename="'+re.sub('[^a-zA-Z0-9._-]','_',f['name'])+'"'});r.raise_for_status()
   with engine.begin() as c:c.execute(update(files).where(files.c.id==f['id']).values(synced=1))
  app.logger.warning('Smartsheet delivery confirmed for ticket %s',tid)
  return True
 except (requests.RequestException,ValueError,KeyError,IndexError) as exc:
  response=getattr(exc,'response',None)
  app.logger.warning('Smartsheet delivery pending for ticket %s; HTTP status %s',tid,getattr(response,'status_code','unavailable'))
  if row:
   with engine.begin() as c:c.execute(update(tickets).where(tickets.c.id==tid).values(status='000 - Intake • attachments awaiting transfer'))
  return False
@app.post('/api/portal')
def save_ticket():
 u=required();rate('ticket:'+u['id'],15);f=request.form
 if f.get('action')=='review':
  with engine.begin() as c:
   t=c.execute(select(tickets.c.id).where(tickets.c.id==f.get('ticketId'),tickets.c.user_id==u['id'])).first()
   if not t:abort(400,description='Select one of your tickets.')
   data=json.dumps(f.to_dict())
   if len(data)>10000:abort(400,description='Please shorten your notes.')
   review_id=secrets.token_hex(16)
   c.execute(insert(reviews).values(id=review_id,user_id=u['id'],ticket_id=t.id,data=data,created=now()))
   feedback='\n'.join(k+': '+f.get(k,'') for k in ('serviceDate','Service technician','Service department','notes','customerName'))
   notifications.enqueue(c,email_outbox,'feedback-'+review_id,'Customer feedback | '+t.id,'Ticket: '+t.id+'\nCustomer: '+u['name']+'\nEmail: '+u['email']+'\n'+feedback,u['email'])
  return {'message':'Your feedback has been saved.'}
 fields=('name','phone','address','category','subject','description');d={k:f.get(k,'').strip() for k in fields}
 if any(not d[k] or len(d[k])>(5000 if k=='description' else 300) for k in fields):abort(400,description='Please complete all fields and keep the description under 5,000 characters.')
 if f.get('ack')!='on':abort(400,description='Please acknowledge the service rates and policies.')
 uploads=[x for x in request.files.getlist('photos') if x.filename];stored=[];total=0
 if len(uploads)>5:abort(400,description='Attach up to 5 files.')
 for x in uploads:
  blob=x.read();total+=len(blob)
  valid=(x.mimetype=='image/jpeg' and blob.startswith(b'\xff\xd8\xff')) or (x.mimetype=='image/png' and blob.startswith(b'\x89PNG\r\n\x1a\n')) or (x.mimetype=='application/pdf' and blob.startswith(b'%PDF-')) or (x.mimetype=='image/webp' and blob[:4]==b'RIFF' and blob[8:12]==b'WEBP')
  if total>20*1024*1024 or not valid:abort(400,description='Use JPG, PNG, WebP or PDF files totaling at most 20 MB.')
  stored.append(dict(id=secrets.token_hex(16),user_id=u['id'],name=x.filename[:200],type=x.mimetype,data=blob,synced=0))
 tid='HOS-'+secrets.token_hex(6).upper()
 with engine.begin() as c:
  c.execute(insert(tickets).values(id=tid,user_id=u['id'],email=u['email'],created=now(),status='Saved • awaiting intake connection',**d))
  for x in stored:c.execute(insert(files).values(ticket_id=tid,**x))
 synced=sync_ticket(tid)
 return {'id':tid,'message':'Ticket submitted to the service intake team.' if synced else 'Request saved securely. Delivery to the service intake team is pending.'}
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
  us=list(c.execute(select(users.c.id,users.c.email,users.c.name,users.c.plan,users.c.membership_start,users.c.membership_end,users.c.minutes_used,users.c.completion_date)).mappings());ts=list(c.execute(select(tickets).order_by(tickets.c.created.desc()).limit(100)).mappings());rs=list(c.execute(select(reviews).order_by(reviews.c.created.desc()).limit(100)).mappings())
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
def retry_pending_intake():
 while True:
  try:
   if engine and smartsheet_config():
    with engine.connect() as c:
     pending=set(c.execute(select(tickets.c.id).where(tickets.c.row_id.is_(None))).scalars())
     pending.update(c.execute(select(files.c.ticket_id).where(files.c.synced==0)).scalars())
    for tid in sorted(pending):sync_ticket(tid)
  except Exception:
   app.logger.warning('Intake retry deferred; requests remain saved')
  time.sleep(60)
if os.getenv('RENDER') and os.getenv('SMARTSHEET_TOKEN'):
 threading.Thread(target=retry_pending_intake,name='intake-delivery',daemon=True).start()
if os.getenv('RENDER') and notifications.configured():
 threading.Thread(target=notifications.run_worker,args=(engine,email_outbox,app.logger),name='staff-email',daemon=True).start()
if __name__=='__main__':app.run(host='0.0.0.0',port=int(os.getenv('PORT','5000')))
