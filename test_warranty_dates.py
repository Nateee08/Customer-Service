import unittest
from warranty_dates import qualifies_warranty,closure_timestamp

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
