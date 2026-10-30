import unittest
from unittest.mock import patch, Mock
import test_app
import app as care

class AddressTests(unittest.TestCase):
 def test_full_address_delivery_and_validation(self):
  client=care.app.test_client()
  test_app.register_verified(client,data={'name':'Address Test','email':'address@test.invalid','password':'test-password-1234'},headers={'Origin':'http://localhost'})
  data={'name':'Address Test','phone':'5550100','address':'123 Service St','address2':'Unit 4 / PO Box 12','city':'Woodland','state':'ca','postal':'95695-1234','category':'Solar','subject':'Address test','description':'Test','ack':'on'}
  with patch.object(care,'sync_ticket',return_value=False):
   r=client.post('/api/portal',data=data,headers={'Origin':'http://localhost'})
  self.assertEqual(r.status_code,200);tid=r.json['id']
  expected='123 Service St\nUnit 4 / PO Box 12\nWoodland, CA 95695-1234'
  with care.engine.connect() as c:self.assertEqual(c.execute(care.select(care.tickets.c.address).where(care.tickets.c.id==tid)).scalar_one(),expected)
  response=Mock();response.json.side_effect=[{'rows':[]},{'result':[{'id':456}]}]
  with patch.object(care,'smartsheet_config',return_value=('token','sheet',{'ticket':1,'address':2,'status':3})),patch.object(care,'smart_request',return_value=response) as api:
   self.assertTrue(care.sync_ticket(tid))
   self.assertEqual(next(x['value'] for x in api.call_args.kwargs['json'][0]['cells'] if x['columnId']==2),expected)
  for field,value in [('address',''),('city',''),('state','California'),('postal','123')]:
   bad={**data,field:value}
   self.assertEqual(client.post('/api/portal',data=bad,headers={'Origin':'http://localhost'}).status_code,400)
