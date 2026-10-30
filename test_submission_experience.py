import io
import secrets
import unittest
from unittest.mock import patch
import test_app
import app as care

class SubmissionExperienceTests(unittest.TestCase):
 def setUp(self):
  self.client=care.app.test_client()
  self.user={'id':'experience-'+secrets.token_hex(8),'name':'Saved Owner','email':'owner@test.invalid',**{k:None for k in ('plan','membership_start','membership_end','minutes_used','completion_date')}}
  self.form=dict(name='Saved Owner',phone='5307125792',address='123 Service St',address2='Unit 4',city='Woodland',state='CA',postal='95695',category='Solar',subject='Test only',description='Mocked delivery',ack='on',request_key=secrets.token_hex(16))
  for p in (patch.object(care,'required',return_value=self.user),patch.object(care,'deliver_intake_now',return_value=False),patch.object(care._portal_snapshot,'get',return_value=(None,False,False))):
   p.start();self.addCleanup(p.stop)
 def post(self,data=None):return self.client.post('/api/portal',data=data or self.form,headers={'Origin':'http://localhost'})
 def test_same_submission_retry_saves_one_ticket_and_one_attachment(self):
  def data():return {**self.form,'photos':(io.BytesIO(b'%PDF-1.7\nfixture'),'work.pdf')}
  a=self.post(data());b=self.post(data())
  self.assertEqual(a.status_code,200);self.assertEqual(a.json['id'],b.json['id'])
  self.assertEqual(a.json['deliveryStatus'],'pending');self.assertEqual(a.json['ticket']['deliveryStatus'],'pending')
  self.assertEqual(len(b.json['ticket']['files']),1)
  with care.engine.connect() as c:
   self.assertEqual(len(c.execute(care.select(care.tickets).where(care.tickets.c.user_id==self.user['id'])).all()),1)
  # A genuinely new request still creates a new dated intake record.
  c=self.post({**self.form,'request_key':secrets.token_hex(16)})
  self.assertNotEqual(a.json['id'],c.json['id'])
 def test_retry_reads_confirmed_delivery_and_preserves_original_submission(self):
  a=self.post().json
  with care.engine.begin() as c:c.execute(care.update(care.tickets).where(care.tickets.c.id==a['id']).values(row_id='123',status='000 - Intake'))
  b=self.post().json
  self.assertEqual(b['deliveryStatus'],'delivered');self.assertEqual(b['ticket']['created'],a['ticket']['created'])
  self.assertIn('received by our service team',b['message'])
 def test_saved_details_are_editable_defaults_from_only_current_account(self):
  self.post()
  with care.engine.begin() as c:c.execute(care.insert(care.tickets).values(id=secrets.token_hex(16),user_id='another-account',email='other@test.invalid',name='Private Other',phone='9999999999',address='Other address',created='2099-01-01T00:00:00+00:00'))
  v=self.client.get('/api/portal').json
  self.assertEqual(v['contact'],dict(name='Saved Owner',phone='(530)712-5792',address='123 Service St',address2='Unit 4',city='Woodland',state='CA',postal='95695'))
  with patch.object(care,'required',return_value={**self.user,'id':'brand-new'}):
   v=self.client.get('/api/portal').json
   self.assertEqual(v['contact']['name'],'Saved Owner');self.assertEqual(v['contact']['phone'],'');self.assertEqual(v['contact']['address'],'')
 def test_only_jpeg_photos_and_pdf_documents_are_accepted(self):
  for name,mime,body,expected in [('photo.JPG','image/jpeg',b'\xff\xd8\xfffixture',200),('doc.pdf','application/pdf',b'%PDF-fixture',200),('photo.png','image/png',b'\x89PNG\r\n\x1a\nfixture',400),('photo.heic','image/heic',b'ftypheic',400),('photo.webp','image/webp',b'RIFF0000WEBP',400),('fake.jpg','image/jpeg',b'not-a-jpeg',400)]:
   with self.subTest(name=name):
    r=self.post({**self.form,'request_key':secrets.token_hex(16),'photos':(io.BytesIO(body),name,mime)})
    self.assertEqual(r.status_code,expected)
 def test_request_keys_are_scoped_to_the_verified_account(self):
  a=self.post().json
  with patch.object(care,'required',return_value={**self.user,'id':'different-owner'}):b=self.post().json
  self.assertNotEqual(a['id'],b['id'])
