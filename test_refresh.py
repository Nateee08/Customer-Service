import unittest,os,threading,time,secrets
from unittest.mock import patch,Mock
import requests
import test_app
import app as care
class RefreshTests(unittest.TestCase):
 def test_cached_config_does_not_wait_for_attachment_lock(self):
  class BusyTransferLock:
   def __enter__(self):raise AssertionError('Refresh waited for an upload')
   def __exit__(self,*args):pass
  with patch.dict(os.environ,{'SMARTSHEET_TOKEN':'test','SMARTSHEET_SHEET_ID':'sheet'}),patch.object(care,'_intake_columns',{'status':55,'ticket':56}),patch.object(care,'_intake_lock',BusyTransferLock()):
   self.assertEqual(care.smartsheet_config()[2]['status'],55)

class SnapshotTests(unittest.TestCase):
 def test_slow_refresh_is_shared_and_does_not_block_saved_data(self):
  cache=care.SheetSnapshot();started=threading.Event();release=threading.Event()
  def load():
   started.set();release.wait(2)
   return {'rows':['complete']}
  loader=Mock(side_effect=load)
  try:
   before=time.monotonic()
   self.assertEqual(cache.get(loader,wait_seconds=.03),(None,False,True))
   self.assertLess(time.monotonic()-before,.5)
   self.assertTrue(started.is_set())
   for _ in range(4):self.assertEqual(cache.get(loader,wait_seconds=0),(None,False,True))
   self.assertEqual(loader.call_count,1)
  finally:
   release.set()
  value,fresh,syncing=cache.get(loader,wait_seconds=1)
  self.assertEqual(value,{'rows':['complete']});self.assertTrue(fresh);self.assertFalse(syncing)
  cache.get(loader);self.assertEqual(loader.call_count,1)

 def test_failed_refresh_keeps_cached_data_but_does_not_claim_current_status(self):
  cache=care.SheetSnapshot(retry_delay=0)
  value={'rows':['previous']}
  self.assertTrue(cache.get(lambda:value)[1])
  cache._updated=time.monotonic()-20
  fail=Mock(side_effect=requests.Timeout())
  self.assertEqual(cache.get(fail),(value,False,False))

class PortalSnapshotTests(unittest.TestCase):
 def setUp(self):
  self.client=care.app.test_client();self.user={'id':'refresh-'+secrets.token_hex(6),**{k:None for k in ('plan','membership_start','membership_end','minutes_used','completion_date')}}
  self.cfg=('test','sheet',{'status':55,'manager':56})
  self.cache=care.SheetSnapshot(retry_delay=0)
  for context in (patch.object(care,'required',return_value=self.user),patch.object(care,'smartsheet_config',return_value=self.cfg),patch.object(care,'_portal_snapshot',self.cache),patch.object(care,'refresh_warranties')):
   context.start();self.addCleanup(context.stop)
 def ticket(self,row_id=None,user_id=None):
  tid='HOS-'+secrets.token_hex(6)
  with care.engine.begin() as db:
   db.execute(care.insert(care.tickets).values(id=tid,user_id=user_id or self.user['id'],email='test@example.invalid',name='Test',phone='5305551234',address='1 Test St',category='Solar',subject='Test',description='Test',created=care.now(),status='000 - Intake' if row_id else 'Saved',row_id=row_id))
  return tid
 def response(self,rows,total=None):
  response=Mock();response.json.return_value={'columns':[{'id':56,'title':'Manager'}],'rows':rows}
  if total is not None:response.json.return_value['totalRowCount']=total
  return response
 def test_repeated_portal_reads_share_snapshot_and_preserve_account_isolation(self):
  tid=self.ticket('101');other=self.ticket('102',user_id='another-account')
  rows=[{'id':101,'cells':[{'columnId':55,'value':'010 - Touched'},{'columnId':56,'value':'02-Alec'}]}, {'id':102,'cells':[{'columnId':55,'value':'000 - Intake'}]}]
  with patch.object(care,'smart_request',return_value=self.response(rows)) as api:
   for _ in range(2):
    result=self.client.get('/api/portal').json
    self.assertTrue(result['connected']);self.assertEqual([t['id'] for t in result['tickets']],[tid])
    self.assertEqual(result['tickets'][0]['manager'],'02-Alec');self.assertEqual(result['tickets'][0]['deliveryStatus'],'delivered')
   self.assertEqual(api.call_count,1)
 def test_slow_smartsheet_returns_saved_ticket_and_profile_without_timeout(self):
  tid=self.ticket('103');release=threading.Event();started=threading.Event()
  def slow(*args,**kwargs):
   started.set();release.wait(4)
   return self.response([{'id':103,'cells':[]}])
  with patch.object(care,'smart_request',side_effect=slow):
   try:
    before=time.monotonic();response=self.client.get('/api/portal')
    self.assertLess(time.monotonic()-before,2.8)
    self.assertEqual(response.status_code,200)
    result=response.json;self.assertTrue(result['syncing']);self.assertFalse(result['connected'])
    self.assertEqual(result['tickets'][0]['id'],tid);self.assertIn('profile',result)
   finally:
    release.set();self.cache.get(care.load_portal_snapshot,wait_seconds=1)
 def test_in_flight_snapshot_cannot_delete_a_ticket_delivered_after_read_started(self):
  tid=self.ticket();release=threading.Event();started=threading.Event()
  def read(*args,**kwargs):
   started.set();release.wait(2)
   return self.response([])
  with patch.object(care,'smart_request',side_effect=read):
   try:
    self.cache.get(care.load_portal_snapshot,wait_seconds=.03)
    self.assertTrue(started.is_set())
    with care.engine.begin() as db:db.execute(care.update(care.tickets).where(care.tickets.c.id==tid).values(row_id='104',status='000 - Intake'))
   finally:
    release.set();self.cache.get(care.load_portal_snapshot,wait_seconds=1)
   result=self.client.get('/api/portal').json
   self.assertEqual(result['tickets'][0]['id'],tid);self.assertEqual(result['tickets'][0]['deliveryStatus'],'delivered')
  # A later complete read still detects a real saved Smartsheet deletion.
  self.cache._updated=time.monotonic()-20
  with patch.object(care,'smart_request',return_value=self.response([])):
   self.assertEqual(self.client.get('/api/portal').json['tickets'],[])
 def test_partial_or_failed_refresh_retains_last_manager_and_ticket(self):
  tid=self.ticket('105');row={'id':105,'cells':[{'columnId':55,'value':'010 - Touched'},{'columnId':56,'value':'02-Alec'}]}
  with patch.object(care,'smart_request',return_value=self.response([row])):
   self.assertTrue(self.client.get('/api/portal').json['connected'])
  for failure in (self.response([],total=10),requests.Timeout()):
   self.cache._updated=time.monotonic()-20
   kwargs={'side_effect':failure} if isinstance(failure,Exception) else {'return_value':failure}
   with patch.object(care,'smart_request',**kwargs):
    result=self.client.get('/api/portal').json
    self.assertFalse(result['connected']);self.assertEqual(result['tickets'][0]['id'],tid)
    self.assertEqual(result['tickets'][0]['manager'],'02-Alec');self.assertEqual(result['tickets'][0]['status'],'010 - Touched')

 def test_background_snapshot_removes_deleted_request_without_portal_visit(self):
  deleted=self.ticket('201');kept=self.ticket('202');pending=self.ticket()
  with patch.object(care,'smart_request',return_value=self.response([{'id':202,'cells':[]}])):
   care.load_portal_snapshot()
  with care.engine.connect() as db:
   statuses=dict(db.execute(care.select(care.tickets.c.id,care.tickets.c.status).where(care.tickets.c.id.in_([deleted,kept,pending]))).all())
  self.assertEqual(statuses[deleted],'Deleted from Smartsheet')
  self.assertEqual(statuses[kept],'000 - Intake')
  self.assertEqual(statuses[pending],'Saved')
 def test_old_snapshot_cannot_restore_confirmed_deleted_request(self):
  tid=self.ticket('203')
  row={'id':203,'cells':[{'columnId':55,'value':'000 - Intake'}]}
  with patch.object(care,'smart_request',return_value=self.response([row])):
   self.assertEqual(len(self.client.get('/api/portal').json['tickets']),1)
  care.reconcile_deleted_tickets({'rows':[]},{tid:'203'})
  self.assertEqual(self.client.get('/api/portal').json['tickets'],[])
 def test_reconcile_rejects_partial_snapshot_and_changed_row_link(self):
  tid=self.ticket('204')
  with self.assertRaises(ValueError):care.reconcile_deleted_tickets({'rows':[],'totalRowCount':1},{tid:'204'})
  care.reconcile_deleted_tickets({'rows':[]},{tid:'old-row'})
  with care.engine.connect() as db:self.assertEqual(db.execute(care.select(care.tickets.c.status).where(care.tickets.c.id==tid)).scalar(),'000 - Intake')

 def test_deleted_ticket_warranty_removed_but_other_coverage_remains(self):
  deleted=self.ticket('301');kept=self.ticket('302')
  with care.engine.begin() as db:
   for tid in (deleted,kept):db.execute(care.insert(care.warranties).values(ticket_id=tid,user_id=self.user['id'],started_at='2026-10-30T20:00:00+00:00'))
  with patch.object(care,'smart_request',return_value=self.response([{'id':302,'cells':[{'columnId':55,'value':'030 - Closed'}]}])):
   result=self.client.get('/api/portal').json
  self.assertEqual([w['ticketId'] for w in result['warranties']],[kept])
  self.assertEqual([t['id'] for t in result['tickets']],[kept])

 def test_phone_and_text_warranties_are_not_displayed(self):
  rows=[]
  for row_id,status in [('401','030 - Closed'),('402','031 - Closed Phone Support'),('403','032 - Closed Text Support')]:
    tid=self.ticket(row_id)
    with care.engine.begin() as db:db.execute(care.insert(care.warranties).values(ticket_id=tid,user_id=self.user['id'],started_at='2026-10-30T20:00:00+00:00'))
    rows.append({'id':int(row_id),'cells':[{'columnId':55,'value':status}]})
  with patch.object(care,'smart_request',return_value=self.response(rows)):
   result=self.client.get('/api/portal').json
  self.assertEqual(len(result['warranties']),1)
