import unittest,os
from unittest.mock import patch
import test_app
import app as care
class RefreshTests(unittest.TestCase):
 def test_cached_config_does_not_wait_for_attachment_lock(self):
  class BusyTransferLock:
   def __enter__(self):raise AssertionError('Refresh waited for an upload')
   def __exit__(self,*args):pass
  with patch.dict(os.environ,{'SMARTSHEET_TOKEN':'test','SMARTSHEET_SHEET_ID':'sheet'}),patch.object(care,'_intake_columns',{'status':55,'ticket':56}),patch.object(care,'_intake_lock',BusyTransferLock()):
   self.assertEqual(care.smartsheet_config()[2]['status'],55)
