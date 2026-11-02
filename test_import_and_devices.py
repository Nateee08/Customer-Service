import secrets,re,time,unittest,datetime
from unittest.mock import patch,Mock
import test_app
import app as a
import intake_import

class ImportTests(unittest.TestCase):
 def setUp(self):
  self.client=a.app.test_client();self.email=secrets.token_hex(8)+'@test.invalid'
  test_app.register_verified(self.client,data={'name':'Alec Cullison','email':self.email,'password':'test-password-1234'},headers={'Origin':'http://localhost'})
  with a.engine.connect() as c:self.user=dict(c.execute(a.select(a.users).where(a.users.c.email==self.email)).mappings().one())
  self.cols={k:i+1 for i,k in enumerate(('email','status','subject','ticket','first_name','last_name','phone','address','description','manager'))}
  self.rid=secrets.randbelow(10**12)
  self.values={'email':self.email.upper(),'status':'020 - Contact Made','subject':'Battery service','ticket':'9184','first_name':'Alec','last_name':'Cullison','phone':'5307125792','address':'1 Service St','description':'PRIVATE STAFF NOTES','manager':'02-Alec'}
  self.snapshot=self.make_snapshot()
 def make_snapshot(self):
  return {'sheet':{'rows':[{'id':self.rid,'createdAt':'2026-10-20T18:00:00Z','cells':[{'columnId':self.cols[k],'value':v} for k,v in self.values.items()]}]},'config':('token','intake',self.cols),'known_rows':{},'checked_at':a.now()}
 def portal(self,snapshot=None):
  cache=a.SheetSnapshot();cache.publish(snapshot or self.snapshot)
  with patch.object(a,'_portal_snapshot',cache),patch.object(a,'_portal_background',True):return self.client.get('/api/portal').json
 def test_existing_row_links_once_with_number_status_manager_and_no_staff_notes(self):
  for _ in range(2):
   data=self.portal();self.assertEqual(len(data['tickets']),1)
   t=data['tickets'][0];self.assertEqual(t['reference'],'HOS-9184');self.assertEqual(t['status'],'020 - Contact Made');self.assertEqual(t['manager'],'02-Alec')
   self.assertNotIn('PRIVATE STAFF NOTES',str(data));self.assertEqual(t['created'],'2026-10-20T18:00:00+00:00')
  with a.engine.connect() as c:self.assertEqual(len(c.execute(a.select(a.tickets).where(a.tickets.c.row_id==str(self.rid))).all()),1)
 def test_unverified_account_cannot_claim_a_smartsheet_ticket(self):
  anonymous=a.app.test_client()
  cache=a.SheetSnapshot();cache.publish(self.snapshot)
  with patch.object(a,'_portal_snapshot',cache):self.assertEqual(anonymous.get('/api/portal').status_code,401)
  with a.engine.connect() as c:self.assertIsNone(c.execute(a.select(a.tickets.c.id).where(a.tickets.c.row_id==str(self.rid))).scalar())
 def test_existing_website_link_is_preserved(self):
  with a.engine.begin() as c:c.execute(a.insert(a.tickets).values(id='web-'+str(self.rid),user_id=self.user['id'],row_id=str(self.rid),email=self.email,name='Alec Cullison',created=a.now(),status='000 - Intake'))
  data=self.portal();self.assertEqual(len(data['tickets']),1);self.assertEqual(data['tickets'][0]['id'],'web-'+str(self.rid))
  with a.engine.connect() as c:self.assertIsNone(c.execute(a.select(a.imported_tickets.c.ticket_id).where(a.imported_tickets.c.ticket_id=='web-'+str(self.rid))).scalar())
 def test_email_required_and_partial_snapshot_cannot_import(self):
  self.values['email']='someone-else@test.invalid';self.assertEqual(self.portal(self.make_snapshot())['tickets'],[])
  partial=self.snapshot.copy();partial['sheet']={**partial['sheet'],'totalRowCount':9}
  self.assertEqual(self.portal(partial)['tickets'],[])
 def test_existing_website_row_and_in_flight_write_are_not_duplicated(self):
  self.values['description']='Website request ID: HOS-private-pending'
  self.assertEqual(self.portal(self.make_snapshot())['tickets'],[])
 def test_deletion_and_email_change_hide_ticket_and_warranty(self):
  tid=self.portal()['tickets'][0]['id']
  with a.engine.begin() as c:c.execute(a.insert(a.warranties).values(ticket_id=tid,user_id=self.user['id'],started_at='2026-10-30T18:00:00+00:00'))
  self.values['email']='different@test.invalid';sheet=self.make_snapshot()['sheet']
  a.reconcile_deleted_tickets(sheet,{tid:str(self.rid)},self.cols['status'],self.cols)
  result=self.portal(self.make_snapshot());self.assertEqual(result['tickets'],[]);self.assertEqual(result['warranties'],[])
 def test_historical_closed_ticket_does_not_get_a_new_feedback_window(self):
  self.values['status']='030 - Closed'
  self.assertEqual(self.portal(self.make_snapshot())['tickets'],[])
  with a.engine.connect() as c:
   tid=c.execute(a.select(a.tickets.c.id).where(a.tickets.c.row_id==str(self.rid))).scalar_one()
   self.assertIsNone(c.execute(a.select(a.closures.c.closed_at).where(a.closures.c.ticket_id==tid)).scalar())
 def test_soc_reference_is_retained_for_repeat_customer_and_counter_reserved(self):
  self.values['ticket']='SOC-90001';self.assertEqual(self.portal(self.make_snapshot())['tickets'][0]['reference'],'SOC-90001')
  with a.engine.connect() as c:
   self.assertEqual(c.execute(a.select(a.customer_numbers.c.base_number).where(a.customer_numbers.c.user_id==self.user['id'])).scalar(),'SOC-90001')
   self.assertGreaterEqual(c.execute(a.select(a.number_counters.c.value).where(a.number_counters.c.name=='soc')).scalar(),90001)

class TrustedDeviceTests(unittest.TestCase):
 def setUp(self):
  self.client=a.app.test_client();self.email=secrets.token_hex(8)+'@test.invalid';self.password='test-password-1234'
  with patch('notifications.send_account_email') as send:
   self.client.post('/register',data={'name':'Owner','email':self.email,'password':self.password},headers={'Origin':'http://localhost'})
  code=re.search(r'code is: (\d{8})',send.call_args.args[2]).group(1)
  self.client.post('/verify-account',data={'code':code,'remember_device':'on'},headers={'Origin':'http://localhost'})
 def login(self,client=None,**kwargs):
  return (client or self.client).post('/login',data={'email':self.email,'password':self.password},headers={'Origin':'http://localhost',**kwargs})
 def test_remembered_device_password_login_rotates_token_without_email(self):
  old=self.client.get_cookie('care_trusted_device').value;self.client.get('/logout')
  with patch('notifications.send_account_email') as send:
   self.assertEqual(self.login().location,'/');send.assert_not_called()
  self.assertNotEqual(self.client.get_cookie('care_trusted_device').value,old)
  other=a.app.test_client();other.set_cookie('care_trusted_device',old)
  with patch('notifications.send_account_email'):
   self.assertEqual(self.login(other).location,'/verify-account')
 def test_new_expired_and_different_browser_require_code(self):
  with patch('notifications.send_account_email'):
   self.assertEqual(self.login(a.app.test_client()).location,'/verify-account')
   self.assertEqual(self.login(**{'User-Agent':'different browser'}).location,'/verify-account')
 def test_expired_device_requires_code(self):
  token=self.client.get_cookie('care_trusted_device').value
  with a.engine.begin() as c:c.execute(a.update(a.trusted_devices).where(a.trusted_devices.c.token==a.digest(token)).values(expires=int(time.time())-1))
  with patch('notifications.send_account_email'):self.assertEqual(self.login().location,'/verify-account')
 def test_settings_require_password_and_can_require_every_login(self):
  response=self.client.get('/account/settings');self.assertEqual(response.status_code,200);self.assertIn(b'Account security',response.data)
  form={'action':'save','every_login':'on','password':'wrong'}
  self.client.post('/account/settings',data=form,headers={'Origin':'http://localhost'})
  self.client.get('/logout')
  with patch('notifications.send_account_email') as send:self.assertEqual(self.login().location,'/');send.assert_not_called()
  form['password']=self.password;self.client.post('/account/settings',data=form,headers={'Origin':'http://localhost'});self.client.get('/logout')
  with patch('notifications.send_account_email'):self.assertEqual(self.login().location,'/verify-account')
 def test_revocation_requires_new_code(self):
  self.client.post('/account/settings',data={'action':'revoke-all','password':self.password},headers={'Origin':'http://localhost'})
  self.client.get('/logout')
  with patch('notifications.send_account_email'):self.assertEqual(self.login().location,'/verify-account')

 def test_wrong_password_cannot_use_remembered_device(self):
  self.client.get('/logout')
  self.assertEqual(self.client.post('/login',data={'email':self.email,'password':'wrong-password-1234'},headers={'Origin':'http://localhost'}).status_code,400)
  self.assertIsNone(self.client.get('/api/me').json['user'])

 def test_password_reset_revokes_remembered_devices(self):
  token=secrets.token_urlsafe(32)
  with a.engine.begin() as c:
   uid=c.execute(a.select(a.users.c.id).where(a.users.c.email==self.email)).scalar_one()
   c.execute(a.insert(a.resets).values(token=a.digest(token),user_id=uid,expires=int(time.time())+600))
  response=self.client.post('/reset-password/'+token,data={'password':'new-password-12345'},headers={'Origin':'http://localhost'})
  self.assertEqual(response.status_code,302)
  with a.engine.connect() as c:self.assertEqual(c.execute(a.select(a.trusted_devices).where(a.trusted_devices.c.user_id==uid)).all(),[])
