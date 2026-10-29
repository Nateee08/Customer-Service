import os,tempfile,unittest,io
_tmp=tempfile.TemporaryDirectory();os.environ['DATABASE_URL']='sqlite:///'+_tmp.name+'/test.db';os.environ.pop('RENDER',None)
import app as care
class PortalTests(unittest.TestCase):
 def setUp(self):self.a=care.app.test_client();self.b=care.app.test_client()
 def register(self,c,email):return c.post('/register',data={'name':'Test User','email':email,'password':'safe-test-password-123'},headers={'Origin':'http://localhost'})
 def test_accounts_and_ticket_isolation(self):
  self.assertEqual(self.register(self.a,'one@test.invalid').status_code,302)
  self.assertEqual(self.register(self.b,'two@test.invalid').status_code,302)
  self.assertEqual(self.a.get('/api/me').json['user']['email'],'one@test.invalid')
  data={'name':'Test','phone':'5550000000','address':'Test address','category':'Solar','subject':'Isolation test','description':'Test only','ack':'on','photos':(io.BytesIO(b'%PDF-1.7\nfixture'),'test.pdf')}
  r=self.a.post('/api/portal',data=data,headers={'Origin':'http://localhost'});self.assertEqual(r.status_code,200)
  rows=self.a.get('/api/portal').json['tickets'];self.assertEqual(len(rows),1);self.assertIsNone(rows[0]['queue'])
  key=rows[0]['files'][0]['key'];self.assertEqual(self.a.get('/api/file?key='+key).status_code,200);self.assertEqual(self.b.get('/api/file?key='+key).status_code,404)
  self.assertEqual(self.b.get('/api/portal').json['tickets'],[])
  self.assertEqual(self.a.get('/staff').status_code,403)
  self.assertEqual(self.a.post('/api/portal',data={'action':'review','ticketId':rows[0]['id']}).status_code,403)
  self.assertEqual(self.b.post('/api/portal',data={'action':'review','ticketId':rows[0]['id']},headers={'Origin':'http://localhost'}).status_code,400)
  self.assertEqual(self.a.get('/logout').status_code,302);self.assertEqual(self.a.get('/api/portal').status_code,401)
 def test_public_pages_and_documents(self):
  self.assertEqual(self.a.get('/').status_code,200)
  for name in care.DOCS:
   response=self.a.get('/documents/'+name)
   self.assertEqual(response.status_code,200)
   self.assertEqual(response.mimetype,'application/pdf')
   self.assertTrue(response.data.startswith(b'%PDF-'))
   self.assertIn('attachment',self.a.get('/documents/'+name+'?download=1').headers['Content-Disposition'])
  self.assertEqual(self.a.get('/api/portal').status_code,401)
  self.assertEqual(self.a.get('/healthz').json['status'],'ok')
 def test_auth_csrf(self):
  self.assertEqual(self.a.post('/register',data={'name':'X','email':'x@test.invalid','password':'safe-test-password-123'}).status_code,403)
  self.assertEqual(self.a.post('/login',data={},headers={'Origin':'https://attacker.invalid'}).status_code,403)
if __name__=='__main__':unittest.main()
