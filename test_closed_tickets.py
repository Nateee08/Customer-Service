import unittest
from unittest.mock import patch, Mock
import requests
import test_app
import app as care

class ClosedTicketsTests(unittest.TestCase):
 def portal(self,client):
  with patch.object(care,'_portal_snapshot',care.SheetSnapshot()):return client.get('/api/portal')
 def test_closed_variants_cached_outage_and_reopening(self):
  client=care.app.test_client()
  test_app.register_verified(client,data={'name':'Closed Test','email':'closed@test.invalid','password':'test-password-1234'},headers={'Origin':'http://localhost'})
  with patch.object(care,'smartsheet_config',return_value=None):
   response=client.post('/api/portal',data={'name':'Closed Test','phone':'5550100','city':'Woodland','state':'CA','postal':'95695','address':'Service address','category':'Solar','subject':'Completed work','description':'Test','ack':'on'},headers={'Origin':'http://localhost'})
  tid=response.json['id']
  with care.engine.begin() as c:c.execute(care.update(care.tickets).where(care.tickets.c.id==tid).values(row_id='123'))
  cfg=('secret','sheet',{'status':55,'manager':999})
  with patch.object(care,'smartsheet_config',return_value=cfg), patch.object(care,'refresh_warranties'):
   for status in ['030 - Closed','031 - Closed Phone Support','032 - Closed Text Support','CLOSED','closed - resolved','Completed','040 - Completed','COMPLETE']:
    r=Mock();r.json.return_value={'rows':[{'id':123,'cells':[{'columnId':55,'value':status}]}]}
    with patch.object(care,'smart_request',return_value=r):
     data=self.portal(client).json
    self.assertEqual(len(data['tickets']),1)
    self.assertTrue(data['tickets'][0]['feedbackUntil'])
    self.assertEqual(data['feedbackTickets'][0]['id'],tid)
   with care.engine.begin() as c:
    c.execute(care.update(care.closures).where(care.closures.c.ticket_id==tid).values(closed_at=int(care.time.time())-259200))
   with patch.object(care,'smart_request',side_effect=requests.ConnectionError()):
    self.assertEqual(self.portal(client).json['tickets'],[])
   r.json.return_value={'columns':[{'id':56,'title':'Manager Assigned'}],'rows':[{'id':123,'cells':[{'columnId':55,'value':'010 - Touched'},{'columnId':56,'objectValue':{'values':[{'name':'02-Alec','email':'test@example.invalid'}]}}]}]}
   with patch.object(care,'smart_request',return_value=r):
    portal=self.portal(client).json
    self.assertEqual(portal['tickets'][0]['id'],tid)
    self.assertEqual(portal['tickets'][0]['manager'],'02-Alec')
    self.assertEqual(portal['feedbackTickets'],[])
    self.assertTrue(portal['updatedAt'])
   # A failed or partial read must never make an active ticket disappear.
   with patch.object(care,'smart_request',side_effect=requests.ConnectionError()):
    self.assertEqual(len(self.portal(client).json['tickets']),1)
   r.json.return_value={'rows':[],'totalRowCount':100}
   with patch.object(care,'smart_request',return_value=r):
    self.assertEqual(len(self.portal(client).json['tickets']),1)
   r.json.return_value={'rows':[],'totalRowCount':0}
   with patch.object(care,'smart_request',return_value=r):
    self.assertEqual(self.portal(client).json['tickets'],[])
   with patch.object(care,'smart_request',side_effect=requests.ConnectionError()):
    self.assertEqual(self.portal(client).json['tickets'],[])
   # A restored row with an active status reappears.
   r.json.return_value={'rows':[{'id':123,'cells':[{'columnId':55,'value':'Incomplete'}]}]}
   with patch.object(care,'smart_request',return_value=r):
    self.assertEqual(len(self.portal(client).json['tickets']),1)
  with care.engine.connect() as c:
   self.assertIsNotNone(c.execute(care.select(care.tickets).where(care.tickets.c.id==tid)).first())
