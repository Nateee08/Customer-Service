import unittest
from unittest.mock import patch, Mock
import requests
import test_app
import app as care

class ClosedTicketsTests(unittest.TestCase):
 def test_closed_variants_cached_outage_and_reopening(self):
  client=care.app.test_client()
  client.post('/register',data={'name':'Closed Test','email':'closed@test.invalid','password':'test-password-1234'},headers={'Origin':'http://localhost'})
  with patch.object(care,'smartsheet_config',return_value=None):
   response=client.post('/api/portal',data={'name':'Closed Test','phone':'5550100','address':'Service address','category':'Solar','subject':'Completed work','description':'Test','ack':'on'},headers={'Origin':'http://localhost'})
  tid=response.json['id']
  with care.engine.begin() as c:c.execute(care.update(care.tickets).where(care.tickets.c.id==tid).values(row_id='123'))
  cfg=('secret','sheet',{'status':55})
  with patch.object(care,'smartsheet_config',return_value=cfg):
   for status in ['030 - Closed','031 - Closed Phone Support','032 - Closed Text Support','CLOSED','closed - resolved']:
    r=Mock();r.json.return_value={'rows':[{'id':123,'cells':[{'columnId':55,'value':status}]}]}
    with patch.object(care,'smart_request',return_value=r):
     data=client.get('/api/portal').json
    self.assertEqual(data['tickets'],[])
    self.assertEqual(data['feedbackTickets'],[{'id':tid,'subject':'Completed work'}])
   with patch.object(care,'smart_request',side_effect=requests.ConnectionError()):
    self.assertEqual(client.get('/api/portal').json['tickets'],[])
   r.json.return_value={'rows':[{'id':123,'cells':[{'columnId':55,'value':'010 - Touched'}]}]}
   with patch.object(care,'smart_request',return_value=r):
    self.assertEqual(client.get('/api/portal').json['tickets'][0]['id'],tid)
  with care.engine.connect() as c:
   self.assertIsNotNone(c.execute(care.select(care.tickets).where(care.tickets.c.id==tid)).first())
