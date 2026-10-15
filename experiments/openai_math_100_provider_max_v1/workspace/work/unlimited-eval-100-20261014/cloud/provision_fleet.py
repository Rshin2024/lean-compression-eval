#!/usr/bin/env python3
"""Provision only this new evaluation's credential-free verifier VMs."""
import argparse,concurrent.futures,datetime,ipaddress,json,pathlib,subprocess,time,urllib.request
ROOT=pathlib.Path(__file__).resolve().parent
SUB='bea88ce4-de82-43a3-8d6b-a082075a8ee4'
RG='lce-om100-unlimited-20261014'
TASK='om100-unlimited-20261014'
REGIONS=['westus2','eastus2','westus3','centralus','eastus','southcentralus','northcentralus','westus','canadacentral','northeurope','uksouth','westeurope']
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def save(path,value):
 tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(value,indent=2)+'\n');tmp.replace(path)
def az(args):
 p=subprocess.run(['az',*args,'--subscription',SUB,'--only-show-errors','-o','json'],capture_output=True,text=True)
 if p.returncode: raise RuntimeError(p.stderr[-6000:])
 return json.loads(p.stdout) if p.stdout.strip() else None
def provision(region,key,cidr,size,retry):
 name='lce-u100-'+region
 out=ROOT/(region+'.json')
 if out.exists():
  old=json.loads(out.read_text())
  if old.get('state')=='created': return old
  if not retry or old.get('state')!='failed' or 'SkuNotAvailable' not in old.get('error',''): raise ValueError('Existing incomplete state must be inspected before retry: '+region)
  if az(['vm','list','-g',RG,'--query',f"[?name=='{name}'].id"]): raise ValueError('Unexpected existing VM')
  out.rename(ROOT/(region+'-failed-'+str(time.time_ns())+'.json'))
 state={'resource_group':RG,'vm_name':name,'region':region,'size':size,'started_at_utc':now(),'state':'provisioning','model_api_credentials_transferred':False,'ssh_source_cidr':cidr,'worker_capacity':(24 if '_E32' in size else 28 if size.startswith('Standard_D') else 48)}
 save(out,state)
 try:
  nsg=name+'-nsg'
  az(['network','nsg','create','-g',RG,'-n',nsg,'--location',region,'--tags','task='+TASK])
  az(['network','nsg','rule','create','-g',RG,'--nsg-name',nsg,'-n','ssh-operator','--priority','1000','--access','Allow','--direction','Inbound','--protocol','Tcp','--source-address-prefixes',cidr,'--source-port-ranges','*','--destination-address-prefixes','*','--destination-port-ranges','22'])
  state['vm_request_at_utc']=now();save(out,state)
  state['result']=az(['vm','create','-g',RG,'-n',name,'--location',region,'--image','Canonical:ubuntu-24_04-lts:server:latest','--size',state['size'],'--admin-username','leanrunner','--authentication-type','ssh','--ssh-key-values',str(key)+'.pub','--nsg',nsg,'--nsg-rule','NONE','--public-ip-sku','Standard','--public-ip-address',name+'-ip','--os-disk-size-gb','512','--storage-sku','Premium_LRS','--security-type','Standard','--custom-data',str(ROOT/'cloud-init.yaml'),'--tags','task='+TASK,'owner=codex-authorized-evaluation','purpose=lean-verification'])
  state['vm_resource']=az(['vm','show','-g',RG,'-n',name,'--query','{id:id,timeCreated:timeCreated,size:hardwareProfile.vmSize,osDisk:storageProfile.osDisk.managedDisk.id}'])
  state['state']='created';state['created_observed_at_utc']=now()
 except Exception as e:
  state['state']='failed';state['error']=str(e)
 save(out,state)
 print(json.dumps({k:state.get(k) for k in ['region','state','error']}),flush=True)
 return state
def main():
 p=argparse.ArgumentParser();p.add_argument('--regions',nargs='+',choices=REGIONS,required=True);p.add_argument('--source-cidr',required=True);p.add_argument('--size',default='Standard_E64as_v5',choices=['Standard_E64as_v5','Standard_E64s_v5','Standard_E64ads_v5','Standard_E64as_v6','Standard_E64s_v6','Standard_E64s_v3','Standard_E64ds_v5','Standard_E64ds_v4','Standard_D64as_v5','Standard_D64s_v5','Standard_D64s_v6','Standard_E32s_v6','Standard_E32as_v5','Standard_E32s_v5']);p.add_argument('--retry-capacity-failure',action='store_true');a=p.parse_args()
 cidr=str(ipaddress.ip_network(a.source_cidr,strict=True))
 if not cidr.endswith('/32'): raise ValueError('Restrict SSH to current operator IPv4 /32')
 key=ROOT/'id_ed25519'
 if not key.exists():subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(key),'-C',TASK],check=True)
 key.chmod(0o600)
 exists=az(['group','exists','-n',RG])
 if exists:
  g=az(['group','show','-n',RG]);
  if g.get('tags',{}).get('task')!=TASK: raise ValueError('Refusing unowned group')
 else: az(['group','create','-n',RG,'--location','westus2','--tags','task='+TASK,'owner=codex-authorized-evaluation','purpose=lean-verification'])
 with concurrent.futures.ThreadPoolExecutor(max_workers=len(a.regions)) as pool:
  states=list(pool.map(lambda r:provision(r,key,cidr,a.size,a.retry_capacity_failure),a.regions))
 save(ROOT/('fleet-'+str(time.time_ns())+'.json'),{'at_utc':now(),'resource_group':RG,'task':TASK,'vms':states,'model_api_credentials_transferred':False})
 if not all(s['state']=='created' for s in states):raise SystemExit(1)
if __name__=='__main__':main()
