#!/usr/bin/env python3
"""Task-only allocation estimate; provider billing, storage/network remain separate."""
import concurrent.futures,datetime,json,pathlib,urllib.parse,urllib.request
ROOT=pathlib.Path(__file__).resolve().parent
REGIONS=['westus2','eastus2','westus3','centralus','eastus','southcentralus','northcentralus','westus','canadacentral','northeurope','uksouth','westeurope']
NOW=datetime.datetime.now(datetime.timezone.utc)
def one(region):
 p=ROOT/(region+'.json')
 if not p.exists():return None
 s=json.loads(p.read_text());vm=s.get('vm_resource',{});start=vm.get('timeCreated')
 if not start:return None
 url='https://prices.azure.com/api/retail/prices?'+urllib.parse.urlencode({'$filter':f"armRegionName eq '{region}' and armSkuName eq '{s['size']}' and priceType eq 'Consumption'"})
 stop_path=ROOT/'cleanup-plan/lifecycle'/(region+'-deallocated.json')
 stop=json.loads(stop_path.read_text()) if stop_path.exists() else None
 end=NOW
 if stop:
  if stop.get('resource_id')!=vm['id'] or stop.get('power_state')!='PowerState/deallocated':raise ValueError('Untrusted allocation-stop receipt')
  end=datetime.datetime.fromisoformat(stop['deallocated_observed_at_utc'].replace('Z','+00:00'))
 r={'region':region,'resource_id':vm['id'],'size':s['size'],'allocation_started_at_utc':start,'observation_at_utc':NOW.isoformat(),'allocation_end_at_utc':end.isoformat() if stop else None,'allocation_state':'closed_deallocated' if stop else 'running_at_observation','currency':'USD','rate_source':url,'compute_hourly_usd':None,'compute_estimate_usd':None}
 try:
  items=json.load(urllib.request.urlopen(url,timeout=30))['Items'];eligible=[x for x in items if x.get('productName','').startswith('Virtual Machines ') and 'Windows' not in x.get('productName','') and 'Spot' not in x.get('skuName','') and 'Low Priority' not in x.get('skuName','') and x.get('armSkuName')==s['size'] and x.get('unitOfMeasure')=='1 Hour' and x.get('type')=='Consumption']
  if len(eligible)==1:
   x=eligible[0];r['compute_hourly_usd']=x['retailPrice'];r['rate_effective_start']=x.get('effectiveStartDate');r['allocation_hours']=(end-datetime.datetime.fromisoformat(start.replace('Z','+00:00'))).total_seconds()/3600;r['compute_estimate_usd']=r['allocation_hours']*x['retailPrice']
  else:r['rate_lookup_status']='ambiguous:'+str(len(eligible))
 except Exception as e:r['rate_lookup_status']=type(e).__name__
 return r
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as p:rows=[x for x in p.map(one,REGIONS) if x]
result={'at_utc':NOW.isoformat(),'resource_group':'lce-om100-unlimited-20261014','basis':'Official Azure retail compute rate multiplied by observed allocation wall time; not an invoice','compute_estimate_usd':sum(x['compute_estimate_usd'] or 0 for x in rows),'compute_allocations_closed':bool(rows) and all(x['allocation_end_at_utc'] for x in rows),'unknown_rate_vm_count':sum(x['compute_estimate_usd'] is None for x in rows),'excluded_unknown_items':['managed disks','private runtime blob storage','public IP addresses','bandwidth','taxes','credits'],'vms':rows}
(ROOT/'cloud-costs.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'vms':len(rows),'compute_estimate_usd':result['compute_estimate_usd'],'unknown_rate_vm_count':result['unknown_rate_vm_count']}))
