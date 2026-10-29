import unittest
from unittest.mock import patch,Mock
import test_app
import app as care
class IntakeTests(unittest.TestCase):
 def test_actual_column_mapping_and_retry(self):
  client=care.app.test_client();client.post('/register',data={'name':'Intake Test','email':'intake@test.invalid','password':'test-password-1234'},headers={'Origin':'http://localhost'})
  with patch.object(care,'smartsheet_config',return_value=None):
   response=client.post('/api/portal',data={'name':'Jane Smith','phone':'5550100','address':'Service address','category':'Solar','subject':'Six panels offline','description':'Monitoring issue','ack':'on'},headers={'Origin':'http://localhost'})
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
