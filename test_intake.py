import unittest
from unittest.mock import patch,Mock
import test_app
import app as care
class IntakeTests(unittest.TestCase):
 def test_actual_column_mapping_and_retry(self):
  client=care.app.test_client();test_app.register_verified(client,data={'name':'Intake Test','email':'intake@test.invalid','password':'test-password-1234'},headers={'Origin':'http://localhost'})
  with patch.object(care,'smartsheet_config',return_value=None):
   response=client.post('/api/portal',data={'name':'Jane Smith','phone':'5550100','city':'Woodland','state':'CA','postal':'95695','address':'Service address','category':'Solar','subject':'Six panels offline','description':'Monitoring issue','ack':'on'},headers={'Origin':'http://localhost'})
  tid=response.json['id'];cols={k:i+1 for i,k in enumerate(['ticket','first_name','last_name','email','phone','address','subject','description','status','queue','date_realized'])}
  calls=[]
  def api(method,path,**kwargs):
   calls.append((method,path,kwargs));r=Mock();r.raise_for_status.return_value=None
   r.json.return_value={'rows':[]} if method=='GET' else {'result':[{'id':123456}]}
   return r
  with patch.object(care,'smartsheet_config',return_value=('secret','123',cols)),patch.object(care,'smart_request',side_effect=api):
   self.assertTrue(care.sync_ticket(tid));self.assertTrue(care.sync_ticket(tid))
  writes=[x for x in calls if x[0]=='POST'];self.assertEqual(len(writes),1)
  values={c['columnId']:c['value'] for c in writes[0][2]['json'][0]['cells']}
  self.assertEqual(values[cols['first_name']],'Jane');self.assertEqual(values[cols['last_name']],'Smith')
  self.assertEqual(values[cols['status']],'000 - Intake');self.assertIn('Monitoring issue',values[cols['description']]);self.assertIn(tid,values[cols['description']])
  self.assertNotIn(cols['queue'],values)
if __name__=='__main__':unittest.main()

class IntakeDetailsTests(unittest.TestCase):
 def ticket(self):
  return dict(id='HOS-DETAILS',name='Jane',email='new-account@example.com',phone='5305551234',address='1 Test Street\nRocklin, CA 95677',created='2026-10-30T20:59:03.711309+00:00',subject='Monitoring issue',description='Please check',category='Solar')
 def test_client_last_name_jobs_and_exact_submission_time(self):
  from test_customer_database import sheet
  ticket=self.ticket();values=care.intake_values(ticket,client_sheet=sheet())
  self.assertEqual(values['first_name'],'Jane');self.assertEqual(values['last_name'],'Owner');self.assertEqual(values['job_number'],'123')
  self.assertEqual(values['date_realized'],'2026-10-30')
  self.assertEqual(values['submitted_at'],'10/30/2026 01:59:03 PM PDT')
  self.assertIn('Submitted at: 10/30/2026 01:59:03 PM PDT',values['description'])
  self.assertEqual(ticket['name'],'Jane');self.assertEqual(values['email'],'new-account@example.com')
 def test_no_database_keeps_original_submission(self):
  values=care.intake_values(self.ticket(),'manual-123')
  self.assertEqual(values['first_name'],'Jane');self.assertEqual(values['last_name'],'');self.assertEqual(values['job_number'],'manual-123')
  self.assertIn('01:59:03 PM PDT',values['submitted_at'])
 def test_all_same_property_job_numbers_are_written_without_selecting_one(self):
  import copy
  from test_customer_database import sheet
  s=sheet();second=copy.deepcopy(s['rows'][0]);second['cells'][3]['value']='456';s['rows'].append(second)
  values=care.intake_values(self.ticket(),client_sheet=s)
  self.assertEqual(values['job_number'],'123 / 456')
  self.assertIn('123, 456',values['description'])
 def test_timestamp_uses_pacific_day_and_daylight_saving(self):
  t=self.ticket();t['created']='2026-11-02T02:30:04Z'
  values=care.intake_values(t)
  self.assertEqual(values['date_realized'],'2026-11-01')
  self.assertEqual(values['submitted_at'],'11/01/2026 06:30:04 PM PST')
 def test_column_discovery_creates_timestamp_and_maps_job(self):
  import os
  titles=['First Name','Last Name','Email','Phone #','Address','Notes:','Status','Date Realized','Job #','Website Ticket #','Website Subject']
  cols=[{'id':i+1,'title':title} for i,title in enumerate(titles)]
  existing=Mock();existing.json.return_value={'data':cols}
  created=Mock();created.json.return_value={'result':[{'id':99,'title':'Website Submitted At'}]}
  with patch.dict(os.environ,{'SMARTSHEET_TOKEN':'test','SMARTSHEET_SHEET_ID':'sheet','SMARTSHEET_COLUMNS':'{}'}),patch.object(care,'_intake_columns',None),patch.object(care,'_intake_retry_at',0),patch.object(care.requests,'get',return_value=existing),patch.object(care.requests,'post',return_value=created) as post:
   cfg=care.smartsheet_config()
   self.assertEqual(cfg[2]['job_number'],9);self.assertEqual(cfg[2]['last_name'],2);self.assertEqual(cfg[2]['submitted_at'],99)
   self.assertEqual(post.call_args.kwargs['json'][0]['type'],'TEXT_NUMBER')
 def test_enrichment_is_sent_to_smartsheet_not_customer_response(self):
  from test_customer_database import sheet
  from sqlalchemy import insert,select
  import secrets
  t=self.ticket();t['id']='HOS-'+secrets.token_hex(6)
  with care.engine.begin() as db:db.execute(insert(care.tickets).values(**t,user_id='test-only',status='Saved'))
  cols={k:i+1 for i,k in enumerate(['ticket','first_name','last_name','job_number','submitted_at','description','status'])}
  get=Mock();get.json.return_value={'rows':[]}
  post=Mock();post.json.return_value={'result':[{'id':654321}]}
  with patch.object(care,'smartsheet_config',return_value=('test','sheet',cols)),patch.object(care.customer_database,'get_sheet',return_value=sheet()),patch.object(care,'smart_request',side_effect=[get,post]) as api:
   self.assertTrue(care.sync_ticket(t['id']))
   vals={cell['columnId']:cell['value'] for cell in api.call_args_list[1].kwargs['json'][0]['cells']}
   self.assertEqual(vals[cols['last_name']],'Owner');self.assertEqual(vals[cols['job_number']],'123');self.assertIn('01:59:03 PM PDT',vals[cols['submitted_at']])
  with care.engine.connect() as db:
   stored=db.execute(select(care.tickets).where(care.tickets.c.id==t['id'])).mappings().one()
   self.assertEqual(stored['name'],'Jane');self.assertEqual(stored['email'],'new-account@example.com')

 def test_optional_timestamp_column_permission_cannot_block_intake(self):
  import os,requests
  titles=['First Name','Last Name','Email','Phone #','Address','Notes:','Status','Date Realized','Job #','Website Ticket #','Website Subject']
  cols=[{'id':i+1,'title':title} for i,title in enumerate(titles)]
  existing=Mock();existing.json.return_value={'data':cols}
  denied=Mock(status_code=403)
  with patch.dict(os.environ,{'SMARTSHEET_TOKEN':'test','SMARTSHEET_SHEET_ID':'sheet','SMARTSHEET_COLUMNS':'{}'}),patch.object(care,'_intake_columns',None),patch.object(care,'_intake_retry_at',0),patch.object(care.requests,'get',return_value=existing),patch.object(care.requests,'post',side_effect=requests.HTTPError(response=denied)):
   cfg=care.smartsheet_config()
   self.assertIsNotNone(cfg)
   self.assertEqual(cfg[2]['job_number'],9);self.assertEqual(cfg[2]['last_name'],2)
   self.assertNotIn('submitted_at',cfg[2])
   self.assertIn('Submitted at: 10/30/2026 01:59:03 PM PDT',care.intake_values(self.ticket())['description'])
