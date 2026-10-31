import os
import secrets
import threading
import time
import unittest
from unittest.mock import Mock, patch

import requests
from sqlalchemy import insert, select
import test_app
import app as care
from test_customer_database import sheet


class ImmediateIntakeTests(unittest.TestCase):
    def setUp(self):
        self.client = care.app.test_client()
        self.user = {'id': 'immediate-' + secrets.token_hex(6), 'email': 'new-account@example.com'}
        self.form = dict(name='Jane', phone='5305551234', address='1 Test Street', city='Rocklin',
                         state='CA', postal='95677', category='Solar', subject='Monitoring issue',
                         description='Please check', ack='on')
        self.cols = {key: i + 1 for i, key in enumerate(
            ['ticket', 'first_name', 'last_name', 'phone', 'email', 'address', 'job_number', 'submitted_at', 'description', 'status'])}
        for context in (
            patch.dict(os.environ, {'SMARTSHEET_TOKEN': 'test-only', 'SMARTSHEET_SHEET_ID': 'test-only'}),
            patch.object(care, 'required', return_value=self.user),
            patch.object(care, 'smartsheet_config', return_value=('test-only', 'test-only', self.cols)),
            patch.object(care.customer_database, 'get_sheet', return_value=sheet()),
        ):
            context.start()
            self.addCleanup(context.stop)

    def submit(self):
        return self.client.post('/api/portal', data=self.form, headers={'Origin': 'http://localhost'})

    def stored(self, tid):
        with care.engine.connect() as db:
            return dict(db.execute(select(care.tickets).where(care.tickets.c.id == tid)).mappings().one())

    def saved_ticket(self, row=None):
        tid = 'HOS-' + secrets.token_hex(6)
        with care.engine.begin() as db:
            db.execute(insert(care.tickets).values(
                id=tid, user_id=self.user['id'], email=self.user['email'], name='Jane', phone='5305551234',
                address='1 Test Street\nRocklin, CA 95677', category='Solar', subject='Monitoring issue',
                description='Please check', created=care.now(), status='Saved', row_id=row))
        return tid

    def test_submission_confirms_enriched_row_before_responding(self):
        read = Mock(); read.json.return_value = {'rows': []}
        write = Mock(); write.json.return_value = {'result': [{'id': 901}]}
        with patch.object(care, 'smart_request', side_effect=[write]) as api:
            response = self.submit()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['deliveryStatus'], 'delivered')
            self.assertEqual(response.json['ticket']['status'], '000 - Intake')
            self.assertEqual(self.stored(response.json['id'])['row_id'], '901')
            values = {c['columnId']: c['value'] for c in api.call_args.kwargs['json'][0]['cells']}
            self.assertEqual(values[self.cols['last_name']], 'Owner')
            self.assertEqual(values[self.cols['job_number']], '123')
            self.assertEqual(values[self.cols['phone']], '(530)555-1234')
            self.assertTrue(values[self.cols['submitted_at']])
            self.assertEqual(response.json['ticket']['name'], 'Jane')
            self.assertNotIn('job_number', response.json['ticket'])
            self.assertEqual(self.stored(response.json['id'])['phone'], '(530)555-1234')
            self.assertTrue(care.sync_ticket(response.json['id']))
            self.assertEqual(api.call_count, 1)

    def test_uncertain_write_is_saved_and_reconciled_without_a_duplicate(self):
        accepted = []
        def api(method, path, **kwargs):
            response = Mock()
            if method == 'GET':
                response.json.return_value = {'rows': accepted}
                return response
            accepted.append({'id': 902, 'cells': kwargs['json'][0]['cells']})
            raise requests.Timeout('Response lost after Smartsheet accepted the row')
        with patch.object(care, 'smart_request', side_effect=api):
            response = self.submit()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['deliveryStatus'], 'pending')
            tid = response.json['id']
            self.assertIsNone(self.stored(tid)['row_id'])
            self.assertTrue(care.sync_ticket(tid))
            self.assertEqual(self.stored(tid)['row_id'], '902')
            self.assertEqual(len(accepted), 1)

    def test_busy_writer_returns_promptly_for_background_retry(self):
        held=threading.Event();release=threading.Event()
        def hold():
            with care._intake_lock:
                held.set();release.wait(2)
        worker=threading.Thread(target=hold);worker.start();held.wait(1)
        try:
            before=time.monotonic()
            self.assertFalse(care.deliver_intake_now('HOS-BUSY'))
            self.assertLess(time.monotonic()-before,.5)
        finally:
            release.set();worker.join(2)

    def test_existing_attachment_upload_cannot_block_new_ticket_row(self):
        old = self.saved_ticket(row='900'); new = self.saved_ticket()
        with care.engine.begin() as db:
            db.execute(insert(care.files).values(id=secrets.token_hex(6), ticket_id=old,
                user_id=self.user['id'], name='test.pdf', type='application/pdf', data=b'%PDF-test', synced=0))
        uploading = threading.Event(); release = threading.Event()
        def api(method, path, **kwargs):
            response = Mock()
            if path.endswith('/attachments'):
                uploading.set(); release.wait(3)
            response.json.return_value = {'rows': []} if method == 'GET' else {'result': [{'id': 903}]}
            return response
        with patch.object(care, 'smart_request', side_effect=api):
            worker = threading.Thread(target=care.sync_ticket, args=(old,)); worker.start()
            try:
                self.assertTrue(uploading.wait(1))
                self.assertTrue(care.deliver_intake_now(new, wait_seconds=1))
                self.assertFalse(release.is_set())
                self.assertEqual(self.stored(new)['row_id'], '903')
            finally:
                release.set(); worker.join(3)

    def test_saturated_delivery_retains_background_retry(self):
        with patch.object(care, '_intake_delivery_slots') as slots, patch.object(care, '_intake_wake') as wake:
            slots.acquire.return_value = False
            self.assertFalse(care.deliver_intake_now('HOS-QUEUED'))
            wake.set.assert_called_once()


class PhoneFormattingTests(unittest.TestCase):
    def test_complete_numbers_are_formatted_and_matching_still_works(self):
        from customer_database import format_phone, intake_lookup
        for value in ('5307125792', '1-530-712-5792', '+1 (530) 712-5792', '(530) 712-5792'):
            self.assertEqual(format_phone(value), '(530)712-5792')
        s = sheet(); s['rows'] = s['rows'][:1]
        result = intake_lookup(s, email='', name='Jane', telephone=format_phone('5305551234'), address='')
        self.assertEqual(result['record']['last_name'], 'Owner')

    def test_other_numbers_and_extensions_are_preserved(self):
        from customer_database import format_phone
        for value in ('5551234', '+44 20 7946 0958', '5305551234 ext 12', '5305551234 x5'):
            self.assertEqual(format_phone(value), value)


if __name__ == '__main__':
    unittest.main()
