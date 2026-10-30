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

class InternalIntakeMatchingTests(unittest.TestCase):
 def lookup(self, s=None, **overrides):
  from customer_database import intake_lookup
  values=dict(email='new-account@example.com',name='Jane',telephone='5305551234',address='1 Test Street\nRocklin, CA 95677')
  values.update(overrides)
  return intake_lookup(s or sheet(),**values)
 def test_different_account_email_and_first_name_still_match(self):
  result=self.lookup()
  self.assertEqual(result['record']['last_name'],'Owner')
  self.assertEqual(result['job_numbers'],['123'])
 def test_each_exact_identifier_can_find_unique_record(self):
  import copy
  single=copy.deepcopy(sheet());single['rows']=single['rows'][:1]
  for identifier in [dict(email='owner@example.com'),dict(telephone='+1 (530) 555-1234'),dict(name='Jane Owner'),dict(address='1 Test St\nRocklin, CA 95677')]:
   values=dict(email='different@example.com',name='Unknown',telephone='9165559999',address='99 Elsewhere Rd\nAuburn, CA 95603')
   values.update(identifier)
   self.assertEqual(self.lookup(single,**values)['job_numbers'],['123'])
 def test_first_name_alone_never_selects_customer(self):
  self.assertIsNone(self.lookup(name='Jane',telephone='',address='',email='unknown@example.com')['record'])
 def test_multiple_properties_require_disambiguation(self):
  self.assertIsNone(self.lookup(name='Jane Owner',telephone='5305551234',address='',email='unknown@example.com')['record'])
  self.assertEqual(self.lookup()['job_numbers'],['123'])
 def test_no_match_keeps_lookup_empty(self):
  self.assertEqual(self.lookup(name='Unknown Person',telephone='9165559999',address='99 Elsewhere Rd\nAuburn, CA 95603'),{'record':None,'job_numbers':[]})
 def test_multiple_jobs_for_same_customer_property_are_all_included(self):
  import copy
  s=sheet();another=copy.deepcopy(s['rows'][0]);another['cells'][3]['value']='456';another['cells'][0]['value']='old@example.com';s['rows'].append(another)
  self.assertEqual(self.lookup(s)['job_numbers'],['123','456'])
  self.assertEqual(self.lookup(s,job_number='456')['job_numbers'],['456'])
 def test_shared_household_phone_uses_name_to_disambiguate(self):
  import copy
  s=sheet();another=copy.deepcopy(s['rows'][0]);another['cells'][1]['value']='John';another['cells'][3]['value']='456';s['rows'].append(another)
  self.assertEqual(self.lookup(s)['job_numbers'],['123'])
  self.assertIsNone(self.lookup(s,name='Unlisted')['record'])
 def test_unit_is_not_ignored_in_address_lookup(self):
  import copy
  s=copy.deepcopy(sheet());s['rows']=s['rows'][:1];s['rows'][0]['cells'][5]['value']='1 Test St Apt 2'
  self.assertIsNone(self.lookup(s,name='',email='',telephone='',address='1 Test St Unit 3\nRocklin, CA 95677')['record'])
  self.assertEqual(self.lookup(s,name='',email='',telephone='',address='1 Test St Unit 2\nRocklin, CA 95677')['job_numbers'],['123'])
