import unittest
from customer_database import match,intake_address,related_job_numbers
from warranty_dates import closure_timestamp,anniversary

def sheet():
 titles=['Email','First Name','Last Name','Job#','Phone #','Address','City','State','ZIP']
 rows=[['owner@example.com','Jane','Owner','123','530-555-1234','1 Test St','Rocklin','CA','95677'],['other@example.com','Jane','Owner','124','530-555-1234','2 Private St','Rocklin','CA','95677']]
 return {'columns':[{'id':i,'title':title} for i,title in enumerate(titles)],'rows':[{'cells':[{'columnId':i,'value':v} for i,v in enumerate(row)]} for row in rows]}
class MatchingTests(unittest.TestCase):
 def test_verification_required_for_every_identifier(self):
  for kind,value in [('name','Jane Owner'),('job_number','123'),('phone','5305551234'),('email','owner@example.com')]:
   self.assertIsNone(match(sheet(),'attacker@example.com',kind,value))
   self.assertEqual(match(sheet(),'owner@example.com',kind,value)['job_number'],'123')
 def test_ambiguous_match_never_guessed(self):
  s=sheet();s['rows'].append(s['rows'][0]);self.assertIsNone(match(s,'owner@example.com'))
 def test_multiple_job_references_private_and_same_property(self):
  import copy
  s=sheet();second=copy.deepcopy(s['rows'][0]);second['cells'][3]['value']='456';s['rows'].append(second)
  self.assertEqual(related_job_numbers(s,'owner@example.com','Jane Owner','1 Test St\nRocklin, CA 95677'),['123','456'])
  self.assertEqual(related_job_numbers(s,'other@example.com','Jane Owner','1 Test St\nRocklin, CA 95677'),[])
  self.assertIsNone(match(s,'owner@example.com'))
 def test_full_address(self):self.assertEqual(intake_address(match(sheet(),'owner@example.com')),'1 Test St\nRocklin, CA 95677')
 def test_closure_history_and_calendar_year(self):
  history={'data':[{'modifiedAt':'2026-10-30T18:19:06Z','value':'030 - Closed'},{'modifiedAt':'2026-10-31T19:00:00Z','value':'031 - Closed Phone Support'},{'modifiedAt':'2026-10-29T17:00:00Z','value':'020 - Contact Made'}]}
  start=closure_timestamp(history);self.assertEqual(start,'2026-10-30T18:19:06+00:00');self.assertEqual(anniversary(start),'2027-10-30T11:19:06-07:00')
  history['data'].append({'modifiedAt':'2026-11-01T00:00:00Z','value':'000 - Intake'});self.assertIsNone(closure_timestamp(history))
  self.assertEqual(anniversary('2024-02-29T20:00:00+00:00'),'2025-02-28T12:00:00-08:00')
if __name__=='__main__':unittest.main()
