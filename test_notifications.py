import unittest, os
from unittest.mock import patch, Mock
from sqlalchemy import create_engine, MetaData, select
import notifications as n

class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://')
        self.meta=MetaData(); self.table=n.define_table(self.meta); self.meta.create_all(self.engine)
        with self.engine.begin() as c:
            n.enqueue(c,self.table,'feedback-123','Feedback','Customer feedback','customer@example.invalid')
    def test_waits_for_sender(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(n.requests,'post') as post:
            n.deliver_pending(self.engine,self.table,Mock()); post.assert_not_called()
        with self.engine.connect() as c:
            self.assertEqual(c.execute(select(self.table.c.state)).scalar_one(),'pending')
    def test_retry_same_key_and_only_two_recipients(self):
        response=Mock(); response.json.return_value={'id':'sent-123'}
        with patch.dict(os.environ,{'RESEND_API_KEY':'test','NOTIFICATION_FROM':'verified@example.invalid'}), patch.object(n.requests,'post',side_effect=[n.requests.Timeout(),response]) as post:
            n.deliver_pending(self.engine,self.table,Mock())
            n.deliver_pending(self.engine,self.table,Mock())
            n.deliver_pending(self.engine,self.table,Mock())
            self.assertEqual(post.call_count,2)
            for call in post.call_args_list:
                self.assertEqual(call.kwargs['json']['to'],['alec@hookedonsolar.com', 'customerservice@hookedonsolar.com'])
                self.assertEqual(call.kwargs['headers']['Idempotency-Key'],'feedback-123')
                self.assertEqual(call.kwargs['json']['reply_to'],'customer@example.invalid')
        with self.engine.connect() as c:
            self.assertEqual(c.execute(select(self.table.c.state)).scalar_one(),'sent')
    def test_google_feedback_routes_and_holds_uncertain_send(self):
        import base64
        from email import message_from_bytes
        token=Mock(); token.json.return_value={'access_token':'test-access'}
        with patch.dict(os.environ,{'GOOGLE_CLIENT_ID':'test','GOOGLE_CLIENT_SECRET':'test','GOOGLE_REFRESH_TOKEN':'test'},clear=True), patch.object(n.requests,'post',side_effect=[token,n.requests.Timeout()]) as post:
            n.deliver_pending(self.engine,self.table,Mock())
            n.deliver_pending(self.engine,self.table,Mock())
            self.assertEqual(post.call_count,2)
            payload=post.call_args.kwargs['json']['raw']
            msg=message_from_bytes(base64.urlsafe_b64decode(payload))
            self.assertEqual(msg['From'],'customerservice@hookedonsolar.com')
            self.assertEqual(msg['To'],'alec@hookedonsolar.com, customerservice@hookedonsolar.com')
            self.assertEqual(msg['Reply-To'],'customer@example.invalid')
        with self.engine.connect() as c:
            self.assertEqual(c.execute(select(self.table.c.state)).scalar_one(),'needs_review')
    def test_service_requests_are_not_emailed(self):
        with self.engine.begin() as c:
            c.execute(self.table.delete())
            n.enqueue(c,self.table,'ticket-123','Ticket','Service request','customer@example.invalid')
        with patch.dict(os.environ,{'RESEND_API_KEY':'test','NOTIFICATION_FROM':'verified@example.invalid'},clear=True), patch.object(n.requests,'post') as post:
            n.deliver_pending(self.engine,self.table,Mock())
            post.assert_not_called()
