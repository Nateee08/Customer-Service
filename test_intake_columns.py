import copy,unittest
from unittest.mock import Mock
import requests
from intake_columns import migrate

class ColumnMigrationTests(unittest.TestCase):
 def sheet(self):
  return {'columns':[{'id':1,'title':'Website Ticket #'},{'id':2,'title':'Website Subject'},{'id':3,'title':'Job #'},{'id':4,'title':'Issue'}],'rows':[{'id':10,'cells':[{'columnId':1,'value':'SOC-00002'},{'columnId':2,'value':'Battery offline'},{'columnId':3,'value':'old job'}]}]}
 def test_backup_copy_verify_then_delete(self):
  sheet=self.sheet();events=[]
  def api(method,path,**kwargs):
   events.append(method)
   if method=='GET':return copy.deepcopy(sheet)
   if method=='PUT':
    sheet['rows'][0]['cells']=[c for c in sheet['rows'][0]['cells'] if c['columnId'] not in (3,4)]+kwargs['json'][0]['cells']
   if method=='DELETE':self.assertGreaterEqual(events.count('GET'),2)
   return {}
  backup=Mock(side_effect=lambda payload:events.append('BACKUP'))
  result=migrate(api,backup)
  self.assertEqual(result,{'moved':1,'removed':2})
  self.assertLess(events.index('BACKUP'),events.index('PUT'))
  self.assertEqual(backup.call_args_list[0].args[0]['rows'][0]['cells'][2]['value'],'old job')
 def test_failed_readback_never_deletes(self):
  api=Mock(return_value=self.sheet())
  with self.assertRaises(ValueError):migrate(api,Mock())
  self.assertFalse(any(c.args[0]=='DELETE' for c in api.call_args_list))
 def test_partial_sheet_never_writes_or_deletes(self):
  sheet=self.sheet();sheet['totalRowCount']=2
  api=Mock(return_value=sheet)
  with self.assertRaises(ValueError):migrate(api,Mock())
  self.assertEqual(api.call_count,1)
 def test_no_old_columns_is_noop(self):
  self.assertEqual(migrate(Mock(return_value={'columns':[],'rows':[]}),Mock()),{'moved':0,'removed':0})
