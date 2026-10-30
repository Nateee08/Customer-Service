import os,tempfile,unittest,re,secrets,time,threading
from unittest.mock import patch
_tmp=tempfile.TemporaryDirectory();os.environ.setdefault('DATABASE_URL','sqlite:///'+_tmp.name+'/test.db');os.environ.pop('RENDER',None)
import app as care
from sqlalchemy import insert,select,update

class SecurityTests(unittest.TestCase):
 def new_account(self):
  client=care.app.test_client();email=secrets.token_hex(8)+'@test.invalid'
  with patch('notifications.send_account_email') as send:
   r=client.post('/register',data={'name':'Test Owner','email':email,'password':'long-test-password-123'},headers={'Origin':'http://localhost'})
  self.assertEqual(r.status_code,302);self.assertEqual(r.location,'/verify-account')
  code=re.search(r'code is: (\d{8})',send.call_args.args[2]).group(1)
  return client,code,email
 def verify(self,c,code):return c.post('/verify-account',data={'code':code},headers={'Origin':'http://localhost'})
 def test_no_access_before_code_and_single_use(self):
  c,code,email=self.new_account();self.assertEqual(c.get('/api/portal').status_code,401)
  challenge=c.get_cookie('care_challenge').value
  self.assertEqual(self.verify(c,code).status_code,302);self.assertEqual(c.get('/api/me').json['user']['email'],email)
  attacker=care.app.test_client();attacker.set_cookie('care_challenge',challenge)
  self.assertEqual(self.verify(attacker,code).status_code,400);self.assertIsNone(attacker.get('/api/me').json['user'])
 def test_attempt_limit_and_expiry(self):
  c,code,email=self.new_account()
  for _ in range(5):self.assertEqual(self.verify(c,'bad').status_code,400)
  self.assertEqual(self.verify(c,code).status_code,400)
  c,code,email=self.new_account()
  with care.engine.begin() as db:db.execute(update(care.challenges).where(care.challenges.c.token==care.digest(c.get_cookie('care_challenge').value)).values(expires=int(time.time())-1))
  self.assertEqual(self.verify(c,code).status_code,400)
 def test_delivery_failure_does_not_log_in(self):
  c=care.app.test_client()
  with patch('notifications.send_account_email',side_effect=RuntimeError()):
   r=c.post('/register',data={'name':'Test','email':secrets.token_hex(8)+'@test.invalid','password':'long-test-password-123'},headers={'Origin':'http://localhost'})
  self.assertEqual(r.status_code,302)
  self.assertEqual(c.get('/api/account-verification').json['state'],'failed')
  self.assertIn('couldn',c.get('/verify-account').text)
  self.assertIsNone(c.get('/api/me').json['user'])
 def test_slow_email_does_not_block_signup_or_grant_access(self):
  c=care.app.test_client();entered=threading.Event();release=threading.Event()
  def slow_send(*args):
   entered.set();release.wait(5)
  with patch.dict(care.app.config,{'ACCOUNT_EMAIL_ASYNC':True}),patch('notifications.send_account_email',side_effect=slow_send) as send:
   try:
    start=time.monotonic()
    r=c.post('/register',data={'name':'Async','email':secrets.token_hex(8)+'@test.invalid','password':'long-test-password-123'},headers={'Origin':'http://localhost'})
    self.assertLess(time.monotonic()-start,2)
    self.assertEqual(r.status_code,302);self.assertTrue(entered.wait(1))
    self.assertEqual(c.get('/api/account-verification').json['state'],'sending')
    self.assertIsNone(c.get('/api/me').json['user'])
    code=re.search(r'code is: (\d{8})',send.call_args.args[2]).group(1)
   finally:
    release.set()
    for thread in threading.enumerate():
     if thread.name=='account-email':thread.join(2)
  self.assertEqual(c.get('/api/account-verification').json['state'],'sent')
  self.assertEqual(self.verify(c,code).status_code,302)
 def test_interrupted_signup_can_resume_only_with_correct_password(self):
  c,old_code,email=self.new_account()
  form={'name':'Changed name','email':email,'password':'wrong-password-12345'}
  with patch('notifications.send_account_email') as send:
   self.assertEqual(c.post('/register',data=form,headers={'Origin':'http://localhost'}).status_code,400)
   send.assert_not_called()
   form['password']='long-test-password-123'
   self.assertEqual(c.post('/register',data=form,headers={'Origin':'http://localhost'}).status_code,302)
   code=re.search(r'code is: (\d{8})',send.call_args.args[2]).group(1)
  self.assertEqual(self.verify(c,old_code).status_code,400)
  self.assertEqual(self.verify(c,code).status_code,302)
  with care.engine.connect() as db:
   self.assertEqual(db.execute(select(care.users.c.name).where(care.users.c.email==email)).scalar_one(),'Test Owner')
 def test_resend_invalidates_old_code_and_expired_delivery_stops_waiting(self):
  c,old_code,_=self.new_account();token=care.digest(c.get_cookie('care_challenge').value)
  with care.engine.begin() as db:
   db.execute(update(care.account_deliveries).where(care.account_deliveries.c.token==token).values(started=int(time.time())-60,state='sending'))
  self.assertEqual(c.get('/api/account-verification').json['state'],'failed')
  with patch('notifications.send_account_email') as send:
   r=c.post('/verify-account/resend',headers={'Origin':'http://localhost'})
   self.assertEqual(r.status_code,302)
   code=re.search(r'code is: (\d{8})',send.call_args.args[2]).group(1)
  self.assertEqual(self.verify(c,old_code).status_code,400)
  self.assertEqual(self.verify(c,code).status_code,302)
  self.assertEqual(c.get('/api/account-verification').json['state'],'expired')
 def test_cross_customer_files_and_reviews(self):
  a,code,_=self.new_account();self.verify(a,code)
  b,code,_=self.new_account();self.verify(b,code)
  uid_a=None
  with care.engine.connect() as db:uid_a=db.execute(select(care.users.c.id).where(care.users.c.email==a.get('/api/me').json['user']['email'])).scalar_one()
  tid=secrets.token_hex(8);fid=secrets.token_hex(8)
  with care.engine.begin() as db:
   db.execute(insert(care.tickets).values(id=tid,user_id=uid_a,email='owner@test.invalid',status='030 - Closed'))
   db.execute(insert(care.files).values(id=fid,ticket_id=tid,user_id=uid_a,data=b'%PDF-test',type='application/pdf',name='private.pdf'))
  self.assertEqual(b.get('/api/file?key='+fid).status_code,404)
  self.assertEqual(b.post('/api/portal',data={'action':'review','ticketId':tid},headers={'Origin':'http://localhost'}).status_code,400)
  self.assertEqual(a.get('/api/file?key='+fid).status_code,200)

if __name__=='__main__':unittest.main()
