import copy
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

import requests
from sqlalchemy import create_engine, select, insert
import test_app
import app as care
import ticket_numbers
from test_customer_database import sheet


class CustomerNumberTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine('sqlite:///' + self.tmp.name + '/numbers.db')
        care.meta.create_all(self.engine)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.engine.dispose)
        context = patch.object(care, 'engine', self.engine)
        context.start(); self.addCleanup(context.stop)
        self.no_match = {'record': None, 'job_numbers': []}
        self.hos = {'record': {'name': 'Jane Owner'}, 'job_numbers': ['78', '12', '56', '34', '12']}

    def assign(self, uid, tid, resolution, plan='None', **dates):
        with self.engine.begin() as db:
            return ticket_numbers.assign(db, care.number_tables,
                dict(user_id=uid, id=tid, created='2026-10-30T20:00:00Z'),
                dict(plan=plan, **dates), resolution)

    def test_requested_formats_repeats_and_shared_soc_sequence(self):
        self.assertEqual(self.assign('a', 'a1', self.hos), 'HOS-12/34/56/78')
        self.assertEqual(self.assign('a', 'a2', self.hos), 'HOS-12/34/56/78')
        self.assertEqual(self.assign('b', 'b1', self.no_match), 'SOC-00001')
        self.assertEqual(self.assign('b', 'b2', self.no_match), 'SOC-00001')
        self.assertEqual(self.assign('a', 'a3', self.hos, 'Gold'), 'HPS-HOS-12/34/56/78')
        self.assertEqual(self.assign('b', 'b3', self.no_match, 'Basic'), 'HPS-SOC-00001')
        self.assertEqual(self.assign('c', 'c1', self.no_match, 'Gold'), 'HPS-SOC-00002')
        # Stored history does not change when a membership later changes.
        self.assertEqual(self.assign('a', 'a3', self.hos, 'None'), 'HPS-HOS-12/34/56/78')

    def test_plan_dates_are_server_controlled_and_evaluated_on_submission_day(self):
        for i, dates in enumerate((dict(membership_start='2026-11-01'), dict(membership_end='2026-10-29'))):
            self.assertEqual(self.assign('a', str(i), self.hos, 'Gold', **dates), 'HOS-12/34/56/78')
        self.assertEqual(self.assign('a','current',self.hos,'Gold',membership_end='2026-10-30'), 'HPS-HOS-12/34/56/78')

    def test_outage_or_known_client_without_job_does_not_allocate_soc(self):
        self.assertIsNone(self.assign('a', 'pending', None))
        self.assertIsNone(self.assign('a', 'pending', {'record': {'name':'Jane'}, 'job_numbers':[]}))
        self.assertEqual(self.assign('b', 'b1', self.no_match), 'SOC-00001')
        self.assertEqual(self.assign('a', 'pending', self.hos), 'HOS-12/34/56/78')

    def test_concurrent_first_requests_have_one_number_per_customer(self):
        def allocate(i):
            return i // 2, self.assign('user-' + str(i // 2), 'request-' + str(i), self.no_match)
        with ThreadPoolExecutor(max_workers=8) as pool:
            values = list(pool.map(allocate, range(20)))
        grouped = {}
        for user, number in values:grouped.setdefault(user, set()).add(number)
        self.assertTrue(all(len(numbers) == 1 for numbers in grouped.values()))
        self.assertEqual({n for _,n in values}, {f'SOC-{i:05d}' for i in range(1,11)})

    def test_hos_matching_takes_precedence_over_previous_soc_for_same_account(self):
        self.assertEqual(self.assign('a','a1',self.no_match),'SOC-00001')
        self.assertEqual(self.assign('a','a2',self.hos),'HOS-12/34/56/78')
        self.assertEqual(self.assign('a','a3',self.no_match),'SOC-00001')

    def stored_ticket(self, tid, row=None, user='owner', created='2026-10-30T20:00:00Z'):
        t=dict(id=tid,user_id=user,email='owner@example.com',name='Jane',phone='5305551234',
            address='1 Test St\nRocklin, CA 95677',category='Solar',subject='Service',
            description='Please check',created=created,status='030 - Closed' if row else 'Saved',row_id=row)
        with self.engine.begin() as db:db.execute(insert(care.tickets).values(**t))
        return t

    def test_repeat_requests_create_two_dated_rows_and_uncertain_retry_dedupes(self):
        self.stored_ticket('private-1')
        self.stored_ticket('private-2', created='2026-10-31T20:00:00Z')
        cols={key:i+1 for i,key in enumerate(('ticket','description','status','submitted_at','job_number'))}
        rows=[]
        def api(method, path, **kwargs):
            r=Mock(status_code=200)
            if method=='GET':r.json.return_value={'rows':rows}
            elif method=='POST':
                rows.append({'id':100+len(rows),'cells':kwargs['json'][0]['cells']})
                raise requests.Timeout('Accepted but response lost')
            return r
        with patch.object(care,'smartsheet_config',return_value=('token','sheet',cols)), patch.object(care.customer_database,'get_sheet',return_value=sheet()), patch.object(care,'smart_request',side_effect=api):
            for tid in ('private-1','private-2'):
                self.assertFalse(care.sync_ticket(tid,include_files=False))
                self.assertTrue(care.sync_ticket(tid,include_files=False))
        self.assertEqual(len(rows),2)
        values=[{c['columnId']:c['value'] for c in r['cells']} for r in rows]
        self.assertEqual([v[cols['ticket']] for v in values],['HOS-123','HOS-123'])
        self.assertNotEqual(values[0][cols['submitted_at']],values[1][cols['submitted_at']])
        self.assertTrue(ticket_numbers.matches_request(rows[0],cols,'private-1'))
        self.assertFalse(ticket_numbers.matches_request(rows[0],cols,'private-2'))
        legacy={'cells':[{'columnId':cols['ticket'],'value':'private-1'}]}
        self.assertTrue(ticket_numbers.matches_request(legacy,cols,'private-1'))

    def test_existing_ticket_and_warranty_reference_backfill_preserves_history(self):
        t=self.stored_ticket('legacy-1',row='321')
        with self.engine.begin() as db:
            db.execute(insert(care.warranties).values(ticket_id=t['id'],user_id='owner',started_at=t['created']))
        cols={'ticket':5}
        with patch.object(care,'smartsheet_config',return_value=('token','sheet',cols)), patch.object(care.customer_database,'get_sheet',return_value=sheet()), patch.object(care,'smart_request',return_value=Mock(status_code=200)) as api:
            care.refresh_ticket_numbers();care.refresh_ticket_numbers()
        api.assert_called_once_with('PUT','/rows',json=[{'id':321,'cells':[{'columnId':5,'value':'HOS-123'}]}])
        with self.engine.connect() as db:
            saved=dict(db.execute(select(care.tickets).where(care.tickets.c.id==t['id'])).mappings().one())
            self.assertEqual(saved['status'],'030 - Closed');self.assertEqual(saved['created'],t['created'])
        user={'id':'owner',**{k:None for k in ('plan','membership_start','membership_end','minutes_used','completion_date')}}
        with patch.object(care,'required',return_value=user),patch.object(care._portal_snapshot,'get',return_value=(None,False,False)):
            result=care.app.test_client().get('/api/portal').json
        self.assertEqual(result['warranties'][0]['reference'],'HOS-123')
        self.assertEqual(result['warranties'][0]['ticketId'],'legacy-1')

    def test_client_lookup_outage_does_not_block_delivery_or_misclassify_hos_as_soc(self):
        self.stored_ticket('outage-1')
        cols={'ticket':5,'description':6,'status':7}
        calls=[]
        def api(method,path,**kwargs):
            calls.append((method,path,kwargs));r=Mock(status_code=200)
            r.json.return_value={'rows':[]} if method=='GET' else {'result':[{'id':654}]}
            return r
        with patch.object(care,'smartsheet_config',return_value=('token','sheet',cols)),patch.object(care,'smart_request',side_effect=api):
            with patch.object(care.customer_database,'get_sheet',side_effect=requests.Timeout()):
                self.assertTrue(care.sync_ticket('outage-1',include_files=False))
            with self.engine.connect() as db:
                self.assertIsNone(db.execute(select(care.request_numbers.c.reference)).scalar())
            with patch.object(care.customer_database,'get_sheet',return_value=sheet()):care.refresh_ticket_numbers()
        self.assertEqual([call[0] for call in calls],['GET','POST','PUT'])
        self.assertEqual(calls[-1][2]['json'][0]['cells'][0]['value'],'HOS-123')

    def test_number_backfill_does_not_recreate_deleted_rows(self):
        self.stored_ticket('deleted-1',row='1')
        self.stored_ticket('remaining-2',row='2')
        response=Mock(status_code=404)
        with patch.object(care,'smartsheet_config',return_value=('token','sheet',{'ticket':5})),patch.object(care.customer_database,'get_sheet',return_value=sheet()),patch.object(care,'smart_request',side_effect=[response,Mock(status_code=200)]) as api:
            care.refresh_ticket_numbers();care.refresh_ticket_numbers()
        self.assertEqual([call.args[0] for call in api.call_args_list],['PUT','PUT'])
        response.raise_for_status.assert_not_called()

    def test_all_customer_jobs_included_but_shared_phone_does_not_expose_other_properties(self):
        s=sheet()
        extra=copy.deepcopy(s['rows'][0]);extra['cells'][3]['value']='456';s['rows'].append(extra)
        result=care.resolve_ticket_client(dict(email='owner@example.com',name='Jane',phone='5305551234',address='1 Test St\nRocklin, CA 95677'),'123',s)
        self.assertEqual(result['job_numbers'],['123','456'])
        s['rows'][1]['cells'][0]['value']='owner@example.com'
        result=care.resolve_ticket_client(dict(email='owner@example.com',name='Jane',phone='5305551234',address='1 Test St\nRocklin, CA 95677'),'123',s)
        self.assertEqual(result['job_numbers'],['123','124','456'])


if __name__=='__main__':unittest.main()
