import unittest
from warranty_dates import qualifies_warranty,closure_timestamp,fetch_history

class WarrantyStatusTests(unittest.TestCase):
 def test_only_completed_codes_start_coverage(self):
  for code in ('030',):
   for value in (code,code+' - Closed',code+'-Customer wording'):
    self.assertTrue(qualifies_warranty(value))
  for code in ('000','010','020','023','024','025','031','032','040','050','051'):
   self.assertFalse(qualifies_warranty(code+' - Completed'))
 def test_uses_status_history_not_detection_date(self):
  for code in ('030',):
   history={'data':[{'modifiedAt':'2026-10-29T20:00:00Z','value':'025 - Scheduled'},{'modifiedAt':'2026-10-31T01:00:00Z','value':code}]}
   self.assertEqual(closure_timestamp(history),'2026-10-31T01:00:00+00:00')
 def test_phone_and_text_do_not_start_warranty(self):
  for status in ('031 - Closed Phone Support','032 - Closed Text Support'):
   self.assertIsNone(closure_timestamp({'data':[{'modifiedAt':'2026-10-31T01:00:00Z','value':status}]}))
 def test_030_date_does_not_start_from_earlier_phone_closure(self):
  history={'data':[{'modifiedAt':'2026-10-29T20:00:00Z','value':'031'},{'modifiedAt':'2026-10-31T01:00:00Z','value':'030'}]}
  self.assertEqual(closure_timestamp(history),'2026-10-31T01:00:00+00:00')

 def test_numeric_timestamp_and_null_value_display_fallback(self):
  import datetime
  milliseconds=datetime.datetime(2026,10,31,1,tzinfo=datetime.timezone.utc).timestamp()*1000
  history={'data':[{'modifiedAt':milliseconds,'value':None,'displayValue':'030 - Closed'}]}
  self.assertEqual(closure_timestamp(history),'2026-10-31T01:00:00+00:00')
 def test_history_pagination_reads_030_transition_across_pages(self):
  pages={1:{'data':[{'modifiedAt':'2026-10-31T01:00:00Z','value':'030'}],'pageNumber':1,'totalPages':2,'totalCount':2},2:{'data':[{'modifiedAt':'2026-10-29T01:00:00Z','value':'030'}],'pageNumber':2}}
  self.assertIsNone(closure_timestamp(pages[1]))
  self.assertEqual(closure_timestamp(fetch_history(pages.get)),'2026-10-29T01:00:00+00:00')

class WarrantySyncTests(unittest.TestCase):
 def test_030_history_is_saved_and_returned_to_customer(self):
  import secrets
  from unittest.mock import patch,Mock
  import test_app
  import app as care
  tid='warranty-'+secrets.token_hex(8);uid='owner-'+secrets.token_hex(8)
  ticket=dict(id=tid,user_id=uid,email='test@example.invalid',row_id='9001',status='030 - Closed',created=care.now())
  with care.engine.begin() as db:db.execute(care.insert(care.tickets).values(**ticket))
  response=Mock();response.json.return_value={'data':[{'modifiedAt':'2026-10-31T00:57:28Z','value':'030 - Closed'},{'modifiedAt':'2026-10-31T00:56:00Z','value':'000 - Intake'}],'totalCount':2}
  cfg=('test','sheet',{'status':1})
  sheet={'rows':[{'id':9001,'modifiedAt':'2026-10-31T00:57:28Z','cells':[{'columnId':1,'value':'030 - Closed'}]}]}
  with patch.object(care.requests,'get',return_value=response):care.refresh_warranties(sheet,cfg,[ticket])
  user={'id':uid,'name':'Test',**{k:None for k in ('plan','membership_start','membership_end','minutes_used','completion_date')}}
  with patch.object(care,'required',return_value=user),patch.object(care._portal_snapshot,'get',return_value=(None,False,False)):
   coverage=care.app.test_client().get('/api/portal').json['warranties']
  self.assertEqual(len(coverage),1);self.assertEqual(coverage[0]['startsAt'],'2026-10-31T00:57:28+00:00')


class FeedbackCalendarTests(unittest.TestCase):
 def test_three_local_days_across_both_daylight_saving_changes(self):
  import datetime
  from warranty_dates import PACIFIC,feedback_deadline
  for start,expected,hours in [
   ('2026-10-30T18:56:16','2026-11-02T18:56:16',73),
   ('2026-03-06T18:56:16','2026-03-09T18:56:16',71),
   ('2026-10-23T18:56:16','2026-10-26T18:56:16',72)]:
   closed=datetime.datetime.fromisoformat(start).replace(tzinfo=PACIFIC)
   deadline=feedback_deadline(closed.timestamp())
   self.assertEqual(datetime.datetime.fromtimestamp(deadline,PACIFIC).replace(tzinfo=None).isoformat(),expected)
   self.assertEqual(deadline-closed.timestamp(),hours*3600)
