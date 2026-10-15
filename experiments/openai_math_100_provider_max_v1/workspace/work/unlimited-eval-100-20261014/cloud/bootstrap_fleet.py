#!/usr/bin/env python3
"""ARM-verify SSH identities and load the hash-pinned runtime on the new fleet."""
import argparse,concurrent.futures,datetime,hashlib,ipaddress,json,os,pathlib,re,subprocess,time
ROOT=pathlib.Path(__file__).resolve().parent
SUB='bea88ce4-de82-43a3-8d6b-a082075a8ee4';RG='lce-om100-unlimited-20261014'
REGIONS=['westus2','eastus2','westus3','centralus','eastus','southcentralus','northcentralus','westus','canadacentral','northeurope','uksouth','westeurope']
IMAGE='sha256:3889fa8d304ace87a58cc24fadb4fd68709c7f27167f75cf33736d9883114978'
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def save(p,data):
 t=p.with_suffix(p.suffix+'.tmp');t.write_text(json.dumps(data,indent=2)+'\n');t.replace(p)
def az(args,env=None):
 p=subprocess.run(['az',*args,'--subscription',SUB,'--only-show-errors','-o','json'],capture_output=True,text=True,env=env)
 if p.returncode:raise RuntimeError(p.stderr[-2000:])
 return json.loads(p.stdout) if p.stdout.strip() else None
def bootstrap(region,signed_url,blob):
 prior=ROOT/(region+'-runtime.json')
 if prior.exists() and json.loads(prior.read_text()).get('state')=='ready':return json.loads(prior.read_text())
 receipt={'region':region,'state':'awaiting_vm','started_at_utc':now(),'api_credentials_transferred':False};path=ROOT/(region+'-runtime.json');save(path,receipt)
 try:
  for _ in range(300):
   statefile=ROOT/(region+'.json')
   if statefile.exists():
    state=json.loads(statefile.read_text())
    if state['state']=='created':break
    if state['state']=='failed':raise RuntimeError('VM provisioning failed; see region JSON')
   time.sleep(3)
  else:raise TimeoutError('VM not ready within15min')
  ip=str(ipaddress.ip_address(state['result']['publicIpAddress']))
  receipt['ip']=ip;receipt['state']='verifying_ssh';save(path,receipt)
  arm=az(['vm','run-command','invoke','-g',RG,'-n',state['vm_name'],'--command-id','RunShellScript','--scripts','ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub'])
  messages='\n'.join(x.get('message','') for x in arm.get('value',[]));fps=set(re.findall(r'SHA256:[A-Za-z0-9+/]+',messages))
  if len(fps)!=1:raise ValueError('ARM fingerprint not unique')
  scan=subprocess.run(['ssh-keyscan','-T','15','-t','ed25519',ip],capture_output=True,text=True,check=True)
  known=ROOT/('known_hosts-'+region);known.write_text(scan.stdout);known.chmod(0o600)
  checked=subprocess.run(['ssh-keygen','-lf',str(known)],capture_output=True,text=True,check=True)
  if set(re.findall(r'SHA256:[A-Za-z0-9+/]+',checked.stdout))!=fps:raise ValueError('SSH fingerprint mismatch')
  receipt['ssh_fingerprint']=next(iter(fps));receipt['ssh_verified_via']='Azure ARM Run Command'
  config=ROOT/('ssh-'+region+'.conf')
  alias='lce-u100-'+region
  config.write_text(f'''Host {alias}\n  HostName {ip}\n  User leanrunner\n  IdentityFile {ROOT/'id_ed25519'}\n  IdentitiesOnly yes\n  UserKnownHostsFile {known}\n  StrictHostKeyChecking yes\n  BatchMode yes\n  ConnectTimeout 15\n  ServerAliveInterval 30\n  ControlMaster auto\n  ControlPersist 180\n  ControlPath /tmp/lce-u100-%C\n''');config.chmod(0o600)
  receipt['state']='loading_runtime';receipt['ssh_alias']=alias;receipt['ssh_config']=str(ROOT/'ssh-config');save(path,receipt)
  # SAS goes through SSH stdin to curl config, never a process argument or log.
  script='''set -eu\ncloud-init status --wait >/dev/null\nif [ -n "${OPENAI_API_KEY:-}${ANTHROPIC_API_KEY:-}" ]; then exit 91; fi\nmkdir -p /home/leanrunner/om100-unlimited\ncd /home/leanrunner/om100-unlimited\ncurl --fail --silent --show-error --config - <<'CURL_CONFIG'\nurl = "'''+signed_url+'''"\noutput = "runtime.tar.zst.part"\nCURL_CONFIG\nprintf '%s  %s\\n' '''+blob['sha256']+''' runtime.tar.zst.part | sha256sum -c -\nmv runtime.tar.zst.part runtime.tar.zst\nzstd -dc runtime.tar.zst | docker image load\ndocker image inspect '''+IMAGE+''' --format '{{.Id}}'\ndocker info --format '{{json .NCPU}} {{json .MemTotal}} {{json .Architecture}}'\n'''
  p=subprocess.run(['ssh','-F',str(config),alias,'bash','-s'],input=script,capture_output=True,text=True,timeout=1200)
  (ROOT/(region+'-bootstrap.log')).write_text(p.stdout+'\n'+p.stderr)
  if p.returncode:raise RuntimeError('Runtime bootstrap failed; see credential-free bootstrap log')
  if IMAGE not in p.stdout:raise ValueError('Pinned image missing from bootstrap output')
  receipt.update(state='ready',ready_at_utc=now(),runtime_image=IMAGE,runtime_archive_sha256=blob['sha256'],remote_root='/home/leanrunner/om100-unlimited',worker_capacity=state.get('worker_capacity',48))
 except Exception as e:receipt.update(state='failed',error=str(e)[-2000:])
 save(path,receipt);print(json.dumps(receipt),flush=True);return receipt
def main():
 p=argparse.ArgumentParser();p.add_argument('--regions',nargs='+',choices=REGIONS,default=REGIONS);args=p.parse_args()
 (ROOT/'ssh-config').write_text(f'Include {ROOT}/ssh-*.conf\n');(ROOT/'ssh-config').chmod(0o600)
 for _ in range(300):
  if (ROOT/'runtime-blob.json').exists():break
  time.sleep(3)
 else:raise TimeoutError('Private runtime upload not ready within15min')
 blob=json.loads((ROOT/'runtime-blob.json').read_text())
 keys=az(['storage','account','keys','list','-g',RG,'-n',blob['account']]);env=os.environ.copy();env['AZURE_STORAGE_ACCOUNT']=blob['account'];env['AZURE_STORAGE_KEY']=keys[0]['value']
 expiry=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(hours=2)).strftime('%Y-%m-%dT%H:%MZ')
 sas=az(['storage','blob','generate-sas','-c',blob['container'],'-n',blob['blob'],'--permissions','r','--expiry',expiry,'--https-only'],env)
 signed=blob['url']+'?'+sas
 with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:rows=list(pool.map(lambda r:bootstrap(r,signed,blob),args.regions))
 save(ROOT/('runtime-fleet-'+str(time.time_ns())+'.json'),{'at_utc':now(),'resource_group':RG,'vms':rows,'api_credentials_transferred':False})
 if any(x['state']!='ready' for x in rows):raise SystemExit(1)
if __name__=='__main__':main()
