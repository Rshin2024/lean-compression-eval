"""Replace only three still-waiting report jobs; leave every active build alone."""
from __future__ import annotations
import io
import json
from pathlib import Path
import subprocess
import tarfile
from concurrent.futures import ThreadPoolExecutor

WORK=Path(__file__).resolve().parents[1];BASE=WORK.parents[1]
REMOTE='/home/leanrunner/om100-unlimited';RWORK=REMOTE+'/work/'+WORK.name
SSH=['ssh','-F',str(WORK/'cloud/ssh-config')]
MODELS={'claude-opus-5-5','claude-sonnet-5-5','claude-fable-5-1'}

PROGRAM='''import fcntl,json,os,pathlib,signal,subprocess,time
work=pathlib.Path(WORK);model=MODEL
state_path=work/'reporting/jobs'/(model+'.json');state=json.loads(state_path.read_text())
if state.get('state')!='waiting_for_complete_model':
 print(json.dumps({'model':model,'action':'unchanged_already_building_or_complete','state':state.get('state')}));raise SystemExit
pid=state['pid'];proc=pathlib.Path('/proc')/str(pid)
if proc.exists():
 argv=(proc/'cmdline').read_bytes().split(b'\\0')
 if str(work/'reporting/cloud_report_job_v2.py').encode() not in argv or model.encode() not in argv:raise RuntimeError('Report PID belongs to another process')
 os.kill(pid,signal.SIGSTOP)
 for i in range(100):
  if not proc.exists() or '\\nState:\\tT' in (proc/'status').read_text():break
  time.sleep(.02)
 current=json.loads(state_path.read_text())
 if current.get('state')!='waiting_for_complete_model' or current.get('pid')!=pid:
  os.kill(pid,signal.SIGCONT);print(json.dumps({'model':model,'action':'unchanged_build_race','state':current.get('state')}));raise SystemExit
 output=pathlib.Path(BASE)/'outputs/lean-compression-eval/runs/om100-max-suite-high-20261014'/('report-'+model)
 if output.exists() and any(output.iterdir()):
  os.kill(pid,signal.SIGCONT);raise RuntimeError('Existing output preserved; no reporter replacement')
 os.kill(pid,signal.SIGTERM);os.kill(pid,signal.SIGCONT)
 for i in range(100):
  if not proc.exists() or '\\nState:\\tZ' in (proc/'status').read_text():break
  time.sleep(.05)
lockfile=work/'reporting/jobs'/(model+'.lock')
with lockfile.open('a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 fcntl.flock(lock,fcntl.LOCK_UN)
log=work/'reporting'/(model+'-parallel-report.log');f=log.open('ab')
p=subprocess.Popen(['python3',str(work/'reporting/cloud_report_job_v3.py'),'--model',model],stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
receipt={'model':model,'action':'waiting_reporter_replaced','old_report_pid':pid,'new_report_pid':p.pid,'model_calls':0,'lean_calls':0,'active_build_interrupted':False,'at_epoch':time.time()}
(work/'reporting/jobs'/(model+'-parallel-activation.json')).write_text(json.dumps(receipt,indent=2)+'\\n');print(json.dumps(receipt))
'''

def main():
    amendment=json.loads((WORK/'reporting/parallel-report-amendment.json').read_text())
    names=list(amendment['source_sha256'])+['reporting/parallel-report-amendment.json',
        'reporting/parallel-full-equivalence.json','reporting/parallel-equivalence.json']
    buffer=io.BytesIO()
    with tarfile.open(fileobj=buffer,mode='w:gz') as tar:
        for name in names:
            path=WORK/name;tar.add(path,arcname=str(path.relative_to(BASE)),recursive=False)
    data=buffer.getvalue();plan=json.loads((WORK/'cloud/verification-fleet-plan.json').read_text())
    rows=[r for r in plan['assignments'] if r['model'] in MODELS]
    def activate(row):
        ssh=[*SSH,row['ssh_alias']]
        p=subprocess.run(ssh+['tar xzf - -C '+REMOTE],input=data,capture_output=True,timeout=60)
        if p.returncode:raise RuntimeError(p.stderr.decode())
        program=PROGRAM.replace('WORK',repr(RWORK)).replace('MODEL',repr(row['model'])).replace('BASE',repr(REMOTE))
        p=subprocess.run(ssh+['python3 -'],input=program,capture_output=True,text=True,timeout=30)
        if p.returncode:raise RuntimeError(p.stderr)
        return json.loads(p.stdout)
    with ThreadPoolExecutor(max_workers=3) as pool:result=list(pool.map(activate,rows))
    target=WORK/'reporting/parallel-report-activation.json';target.write_text(json.dumps({'models':result,'model_calls':0,'lean_calls':0},indent=2)+'\n')
    print(json.dumps(result))

if __name__=='__main__':main()
