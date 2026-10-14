#!/usr/bin/env python3
"""Loopback-only evaluation progress. Only allowlisted status files are read.

No directory serving, graph reads, provider calls, credential access, raw records,
or model outputs. Counts are copied through a strict allowlist.
"""
import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import time

HERE = Path(__file__).resolve().parent
WORK = HERE.parent
MODELS = [('gpt-6.1-sol','GPT-6.1 Sol'),('gpt-6-sol','GPT-6 Sol'),
          ('gpt-6-luna','GPT-6 Luna'),('gpt-6-astra','GPT-6 Astra'),
          ('claude-fable-5-1','Claude Fable 5.1'),('claude-opus-5-5','Claude Opus 5.5'),
          ('claude-sonnet-5-5','Claude Sonnet 5.5'),('claude-haiku-5-5','Claude Haiku 5.5')]


def bounded_json(path):
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 262144:
            return None
        value = json.loads(path.read_text())
        return value if isinstance(value,dict) else None
    except (OSError,ValueError):
        return None


def integer(value,maximum):
    return value if type(value) is int and 0 <= value <= maximum else None


def timestamp(value):
    return value if type(value) in (int,float) and math.isfinite(value) and 0 < value < time.time()+60 else None


def count(source,key,model):
    values=source.get(key)
    return integer(values.get(model),1200) if isinstance(values,dict) else None


def latest_count(*values):
    present=[v for v in values if v is not None]
    return max(present) if present else None


def total(values):
    return sum(values) if all(v is not None for v in values) else None


def sanitized_status():
    local=bounded_json(WORK/'status.json') or {}
    embedded=local.get('cloud') if isinstance(local.get('cloud'),dict) else {}
    separate=bounded_json(WORK/'cloud-status.json') or {}
    cloud=max([embedded,separate],key=lambda value:timestamp(value.get('updated_at')) or 0)
    revision=bounded_json(WORK/'verification-status.json') or {}
    amended=revision.get('version')=='v2_threads1'
    historical=cloud
    if amended:
        cloud=revision
    now=time.time()
    local_time=timestamp(local.get('updated_at'))
    cloud_time=timestamp(cloud.get('updated_at'))
    rows=[]
    for model,label in MODELS:
        saved=count(revision,'saved',model) if amended else count(local,'checked',model)
        rows.append({'id':model,'label':label,'scheduled':1200,
            'generated':latest_count(count(local,'generated',model),count(cloud,'generated',model)),
            'checked':latest_count(saved,count(cloud,'checked',model)),
            'saved_locally':saved})
    generated=total([row['generated'] for row in rows]);checked=total([row['checked'] for row in rows])
    saved=total([row['saved_locally'] for row in rows])
    blocked=len(local.get('blocked_shards',{})) if isinstance(local.get('blocked_shards'),dict) else 0
    errors=len(cloud.get('errors',{})) if isinstance(cloud.get('errors'),dict) else 0
    sync_problem=local.get('sync_error') is not None or (amended and revision.get('sync_error') is not None)
    stale=local_time is None or now-local_time>90 or cloud_time is None or now-cloud_time>90
    preparing=amended and revision.get('state')=='preparing'
    if preparing:state='Preparing checker update'
    elif generated is None or checked is None: state='Awaiting status'
    elif blocked or errors or sync_problem:state='Needs attention'
    elif generated==checked==9600:state='Checks complete' if saved==9600 else 'Finishing result sync'
    elif stale:state='Update delayed'
    else:state='Running'
    return {'schema_version':1,'state':state,'scheduled':9600,'questions':100,'conditions':3,'samples':4,
        'model_count':8,'generated_total':generated,'checked_total':checked,'saved_total':saved,
        'models':rows,'settings':{'reasoning_effort':'high','max_output_tokens':8192,'azure_cpus':64,'azure_memory_gib':512},
        'workers':{'generation_active':integer(local.get('active_generation_workers'),64),
                   'checker_active':integer(cloud.get('active_workers'),48),'checker_capacity':48},
        'sources':{'generation_updated_at':local_time,'checker_updated_at':cloud_time,
                   'latest_update_at':max([t for t in [local_time,cloud_time] if t is not None],default=None),
                   'stale':stale,'refresh_seconds':25},
        'issues':{'blocked_shards':blocked,'checker_error_groups':errors,'result_sync_problem':sync_problem},
        'verification':{'version':'v2_threads1' if amended else 'v1','preparing':preparing,
            'earlier_checks':integer(historical.get('checked_total'),9600) if amended else None,
            'note':('Every saved response is being checked again with an explicit Lean thread limit. Earlier checks remain preserved; no model responses are regenerated.' if amended else None)},
        'served_at':datetime.now(timezone.utc).isoformat(),
        'count_scope':'Latest recorded local/cloud counts for the frozen cohort; no validity or compression ranking. Local result synchronization can lag cloud checking.'}


class Handler(BaseHTTPRequestHandler):
    server_version='EvaluationProgress'
    sys_version=''
    def do_GET(self):self.serve(False)
    def do_HEAD(self):self.serve(True)
    def serve(self,head):
        if self.path in ('/','/index.html'):
            content=(HERE/'index.html').read_bytes();kind='text/html; charset=utf-8';status=200
        elif self.path=='/status.json':
            content=json.dumps(sanitized_status(),ensure_ascii=False,allow_nan=False,separators=(',',':')).encode();kind='application/json; charset=utf-8';status=200
        else:
            content=b'Not found\n';kind='text/plain; charset=utf-8';status=404
        self.send_response(status)
        self.send_header('Content-Type',kind)
        self.send_header('Content-Length',str(len(content)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        if not head:self.wfile.write(content)
    def log_message(self,format,*args):pass


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--port',type=int,default=62771);args=parser.parse_args()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    print(json.dumps({'url':f'http://127.0.0.1:{server.server_port}/','routes':['/','/index.html','/status.json']}),flush=True)
    server.serve_forever(poll_interval=.5)


if __name__=='__main__':main()
