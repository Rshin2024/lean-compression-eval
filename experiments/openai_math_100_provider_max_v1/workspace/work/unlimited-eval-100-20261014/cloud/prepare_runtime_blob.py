#!/usr/bin/env python3
"""Private runtime transport. Access keys stay only in the local child environment."""
import datetime,hashlib,json,os,pathlib,subprocess,time
ROOT=pathlib.Path(__file__).resolve().parent
SUB='bea88ce4-de82-43a3-8d6b-a082075a8ee4';RG='lce-om100-unlimited-20261014';TASK='om100-unlimited-20261014'
ACCOUNT='lceom100u2610148ee4';CONTAINER='runtime';BLOB='lean-runtime-v2.tar.zst'
ARCHIVE=ROOT.parents[1]/'full-eval-100/runtime/image-archives/lean-compression-runtime-v1-v2_threads1.tar.zst'
HASH='add8381dcca92de900adff3397bd063a997cd797146878588f96f3c4c29f9975'
def az(args,env=None):
 p=subprocess.run(['az',*args,'--subscription',SUB,'--only-show-errors','-o','json'],capture_output=True,text=True,env=env)
 if p.returncode:raise RuntimeError('Azure command failed: '+p.stderr[-2000:])
 return json.loads(p.stdout) if p.stdout.strip() else None
h=hashlib.sha256()
with ARCHIVE.open('rb') as f:
 for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
if h.hexdigest()!=HASH:raise ValueError('Runtime archive hash mismatch')
for _ in range(60):
 if az(['group','exists','-n',RG]):break
 time.sleep(2)
a=az(['storage','account','create','-g',RG,'-n',ACCOUNT,'-l','westus2','--sku','Standard_LRS','--kind','StorageV2','--min-tls-version','TLS1_2','--allow-blob-public-access','false','--https-only','true','--tags','task='+TASK,'owner=codex-authorized-evaluation'])
keys=az(['storage','account','keys','list','-g',RG,'-n',ACCOUNT])
env=os.environ.copy();env['AZURE_STORAGE_KEY']=keys[0]['value'];env['AZURE_STORAGE_ACCOUNT']=ACCOUNT
az(['storage','container','create','-n',CONTAINER,'--public-access','off'],env)
az(['storage','blob','upload','-c',CONTAINER,'-n',BLOB,'--file',str(ARCHIVE),'--overwrite','false','--max-connections','8'],env)
state={'state':'uploaded_private','resource_group':RG,'account':ACCOUNT,'account_id':a['id'],'container':CONTAINER,'blob':BLOB,'url':f'https://{ACCOUNT}.blob.core.windows.net/{CONTAINER}/{BLOB}','sha256':HASH,'bytes':ARCHIVE.stat().st_size,'at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'access':'private-container, short-lived per-blob read SAS held in memory','api_credentials_used':False}
(ROOT/'runtime-blob.json').write_text(json.dumps(state,indent=2)+'\n')
print(json.dumps(state),flush=True)
