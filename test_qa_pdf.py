import unittest,io,base64,json,os
from unittest.mock import Mock,patch
from email import policy
from email.parser import BytesParser
from sqlalchemy import create_engine,MetaData
from pypdf import PdfReader
import qa_pdf,notifications as n
class QaPdfTests(unittest.TestCase):
 def test_original_pdf_fields_and_pdf_only_email(self):
  pdf=qa_pdf.completed({'customerName':'Customer Test','Service technician':'Exceeds','Service department':'Meets','Notified on the way':'Yes'})
  r=PdfReader(io.BytesIO(pdf));fields=r.get_fields()
  self.assertEqual(fields['customerName']['/V'],'Customer Test')
  self.assertEqual(fields['Service technician']['/V'],'/Exceeds')
  self.assertEqual(fields['Notified on the way']['/V'],'/Yes')
  self.assertTrue(r.pages[0].images)
  for ref in r.pages[0]['/Annots']:
   w=ref.get_object();self.assertTrue(w['/AP']['/N'])
   if w.get('/Parent'):
    parent=w['/Parent'].get_object();self.assertIn(w['/AS'],[parent.get('/V'),'/Off'])
  e=create_engine('sqlite://');m=MetaData();t=n.define_table(m);m.create_all(e)
  with e.begin() as c:n.enqueue(c,t,'feedback-pdf','Customer feedback - Customer Test - HOS-TEST',json.dumps({'kind':'qa_pdf','pdf':base64.b64encode(pdf).decode(),'filename':'feedback.pdf'}),'customer@test.invalid')
  token=Mock();token.json.return_value={'access_token':'test'};response=Mock();response.json.return_value={'id':'sent-test'}
  with patch.dict(os.environ,{'GOOGLE_CLIENT_ID':'test','GOOGLE_CLIENT_SECRET':'test','GOOGLE_REFRESH_TOKEN':'test'},clear=True),patch.object(n.requests,'post',side_effect=[token,response]) as post:
   n.deliver_pending(e,t,Mock());msg=BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(post.call_args.kwargs['json']['raw']))
  self.assertEqual(msg['To'],'alec@hookedonsolar.com, customerservice@hookedonsolar.com')
  self.assertEqual(msg['Subject'],'Customer feedback - Customer Test - HOS-TEST')
  attachments=list(msg.iter_attachments());self.assertEqual(len(attachments),1);self.assertEqual(attachments[0].get_content_type(),'application/pdf');self.assertEqual(attachments[0].get_payload(decode=True),pdf)
  self.assertEqual(msg.get_body(preferencelist=('plain',)).get_content().strip(),'')
