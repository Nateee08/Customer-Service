import unittest
from unittest.mock import patch, Mock
from werkzeug.datastructures import MultiDict
import test_app
import app as care

class MultiSystemTests(unittest.TestCase):
 def test_multiple_selections_validation_and_smartsheet_notes(self):
  client=care.app.test_client()
  test_app.register_verified(client,data={'name':'Systems Test','email':'systems@test.invalid','password':'test-password-1234'},headers={'Origin':'http://localhost'})
  base={'name':'Systems Test','phone':'5550100','city':'Woodland','state':'CA','postal':'95695','address':'Service address','subject':'Combined issue','description':'Test request','ack':'on'}
  for choices,expected in [(['Solar','Battery'],'Solar, Battery'),(['Solar','Monitoring'],'Solar, Monitoring'),(['Battery'],'Battery'),(['Solar','Solar','Battery'],'Solar, Battery')]:
   data=MultiDict(base)
   for choice in choices:data.add('category',choice)
   with patch.object(care,'deliver_intake_now',return_value=False):
    response=client.post('/api/portal',data=data,headers={'Origin':'http://localhost'})
   self.assertEqual(response.status_code,200)
   tid=response.json['id']
   with care.engine.connect() as c:
    self.assertEqual(c.execute(care.select(care.tickets.c.category).where(care.tickets.c.id==tid)).scalar_one(),expected)
   r=Mock();r.json.side_effect=[{'result':[{'id':123}]}]
   with patch.object(care,'smartsheet_config',return_value=('token','sheet',{'ticket':1,'description':2,'status':3})),patch.object(care,'smart_request',return_value=r) as api:
    self.assertTrue(care.sync_ticket(tid))
    cells=api.call_args.kwargs['json'][0]['cells']
    self.assertIn('Systems: '+expected,next(cell['value'] for cell in cells if cell['columnId']==2))
  for choices in [[],['Invalid'],['Solar','Invalid']]:
   data=MultiDict(base)
   for choice in choices:data.add('category',choice)
   response=client.post('/api/portal',data=data,headers={'Origin':'http://localhost'})
   self.assertEqual(response.status_code,400)
 def test_fresh_login_page_and_versioned_assets(self):
  client=care.app.test_client();r=client.get('/')
  self.assertEqual(r.headers['Cache-Control'],'no-store')
  self.assertIn(b'/static/portal.js?v=',r.data)
  self.assertIn(b'/static/portal.css?v=',r.data)
  self.assertEqual(client.get('/api/me').headers['Cache-Control'],'no-store')
