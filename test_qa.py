import unittest
from unittest.mock import patch
import test_app
import app as care

class QaWorkflowTests(unittest.TestCase):
 def test_fast_save_and_closed_only_qa(self):
  client=care.app.test_client()
  test_app.register_verified(client,data={'name':'QA Test','email':'qa-flow@test.invalid','password':'test-password-123'},headers={'Origin':'http://localhost'})
  headers={'Origin':'http://localhost'}
  with patch.object(care,'sync_ticket') as sync:
   r=client.post('/api/portal',data={'name':'QA Test','phone':'5550100','address':'Test address','city':'Woodland','state':'CA','postal':'95695','category':'Solar','subject':'QA work','description':'Test','ack':'on'},headers=headers)
   self.assertEqual(r.status_code,200);sync.assert_not_called()
  tid=r.json['id'];self.assertEqual(r.json['ticket']['id'],tid)
  self.assertEqual(client.get('/qa/template?ticketId='+tid).status_code,403)
  self.assertEqual(care.app.test_client().get('/qa/template?ticketId='+tid).status_code,401)
  form={'action':'review','ticketId':tid,'customerName':'QA Test','signatureDate':'2026-10-30','serviceDate':'2026-10-30','Service technician':'Meets','Service department':'Exceeds','notes':'QA notes'}
  self.assertEqual(client.post('/api/portal',data=form,headers=headers).status_code,400)
  with care.engine.begin() as c:
   c.execute(care.update(care.tickets).where(care.tickets.c.id==tid).values(status='030 - Closed'))
   c.execute(care.insert(care.closures).values(ticket_id=tid,closed_at=int(care.time.time())))
  self.assertEqual(client.get('/qa/template?ticketId='+tid).status_code,200)
  self.assertEqual(client.get('/qa/template?ticketId=someone-else').status_code,404)
  with patch.object(care.notifications,'configured',return_value=False):
   self.assertEqual(client.post('/api/portal',data=form,headers=headers).status_code,200)
  with care.engine.connect() as c:
   row=c.execute(care.select(care.email_outbox).where(care.email_outbox.c.subject=='Customer feedback - QA Test - '+tid)).mappings().one()
   self.assertIn('alec@hookedonsolar.com',row['recipients']);self.assertIn('customerservice@hookedonsolar.com',row['recipients']);self.assertIn('qa_pdf',row['body'])
  with care.engine.begin() as c:c.execute(care.update(care.closures).where(care.closures.c.ticket_id==tid).values(closed_at=int(care.time.time())-259201))
  self.assertEqual(client.post('/api/portal',data=form,headers=headers).status_code,400)

 def test_documents_are_readonly(self):
  import io
  from pypdf import PdfReader
  client=care.app.test_client()
  for name in care.DOCS:
   response=client.get('/documents/'+name)
   reader=PdfReader(io.BytesIO(response.data))
   self.assertFalse(reader.get_fields())
   self.assertTrue(reader.pages[0].extract_text().strip())
   for page in reader.pages:
    self.assertFalse(any(a.get_object().get('/Subtype')=='/Widget' for a in page.get('/Annots',[])))
