"""Exercise the real Gunicorn preload/fork lifecycle with isolated fake sync work."""
import json, os, pathlib, socket, subprocess, sys, tempfile, time, unittest
import urllib.request

class WorkerStartupTests(unittest.TestCase):
 def test_preloaded_worker_receives_its_own_snapshot(self):
  with tempfile.TemporaryDirectory() as temp:
   pathlib.Path(temp,'probe.py').write_text('''import os
import app as care
assert care._background_pid is None
os.environ['RENDER']='true'
os.environ['SMARTSHEET_TOKEN']='test-only'
care.notifications.configured=lambda:False
def publish():care._portal_snapshot.publish({'worker':os.getpid()})
care.refresh_portal_loop=publish
care.refresh_warranty_loop=lambda:None
care.warm_client_cache=lambda:None
care.check_client_database=lambda:None
care.retry_pending_intake=lambda:None
app=care.app
@app.get('/probe')
def probe():return {'pid':os.getpid(),'background':care._background_pid,'snapshot':care._portal_snapshot.peek()[0]}
''')
   with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
   env={**os.environ,'PYTHONPATH':temp+os.pathsep+os.getcwd(),'DATABASE_URL':'sqlite:///'+temp+'/probe.db'}
   for key in ('RENDER','SMARTSHEET_TOKEN','GUNICORN_CMD_ARGS'):env.pop(key,None)
   with tempfile.TemporaryFile() as logs:
    process=subprocess.Popen([sys.executable,'-m','gunicorn','probe:app','--preload','--workers','1','--worker-class','gthread','--threads','2','--bind',f'127.0.0.1:{port}'],env=env,stdout=logs,stderr=logs)
    try:
     result=None
     for _ in range(60):
      try:
       with urllib.request.urlopen(f'http://127.0.0.1:{port}/probe',timeout=.5) as response:result=json.load(response)
       if result.get('snapshot'):break
      except OSError:pass
      time.sleep(.1)
     self.assertIsNotNone(result)
     self.assertNotEqual(result['pid'],process.pid)
     self.assertEqual(result['background'],result['pid'])
     self.assertEqual(result['snapshot']['worker'],result['pid'])
    finally:
     process.terminate()
     try:process.wait(timeout=5)
     except subprocess.TimeoutExpired:process.kill();process.wait()
