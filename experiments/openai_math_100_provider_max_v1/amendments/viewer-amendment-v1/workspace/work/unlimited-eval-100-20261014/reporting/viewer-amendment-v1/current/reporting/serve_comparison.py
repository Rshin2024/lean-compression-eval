#!/usr/bin/env python3
"""Serve the completed comparison on loopback with an exact artifact allowlist.

Raw model responses, run records, credentials and directory listings have no
route. This is a local viewer, not a publication service.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlsplit

from build_comparison import MODELS

REPORT_FILES = (
    'report.html', 'analysis.json', 'costs.json', 'cost_attempts.csv',
    'cost_summary.csv', 'correctness.csv', 'correctness_by_stratum.csv',
    'contrasts_by_stratum.csv', 'paired_tasks.csv', 'resource_usage.csv',
    'verification-phase.json', 'verification-policy.json', 'phase-audit-index.json',
)
OPTIONAL_SUITE_FILES = ('operational-disclosure.json', 'transport-cost-disclosure.json',
                        'suite-build-receipt.json')


def routes_for(suite):
    suite = Path(suite).resolve(strict=True)
    names = ['index.html', 'comparison-data.json', 'questions.html', 'questions-data.json']
    names += [name for name in OPTIONAL_SUITE_FILES if (suite / name).exists() or (suite / name).is_symlink()]
    names += [f'report-{model}/{name}' for model in MODELS for name in REPORT_FILES]
    routes = {}
    for name in names:
        path = suite / name
        if not path.is_file() or any(p.is_symlink() for p in [path, *path.parents] if p != suite):
            raise ValueError(f'Missing or linked report artifact: {name}')
        if not path.resolve().is_relative_to(suite):
            raise ValueError(f'Report artifact is outside the suite: {name}')
        routes['/' + name] = path
    routes['/'] = routes['/index.html']
    return routes


def handler_for(routes):
    targets = {route: path.resolve(strict=True) for route, path in routes.items()}
    class Handler(BaseHTTPRequestHandler):
        server_version = 'LocalEvaluationReport'
        sys_version = ''

        def do_GET(self):
            self.serve(False)

        def do_HEAD(self):
            self.serve(True)

        def serve(self, head):
            allowed_hosts = {f'127.0.0.1:{self.server.server_port}',
                             f'localhost:{self.server.server_port}'}
            if self.headers.get('Host', '').lower() not in allowed_hosts:
                self.send_error(403)
                return
            url = urlsplit(self.path)
            path = routes.get(url.path) if not url.scheme and not url.netloc else None
            if (path is None or not path.is_file() or path.is_symlink()
                    or path.resolve() != targets.get(url.path)):
                self.send_error(404)
                return
            kind = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
            with path.open('rb') as stream:
                self.send_response(200)
                self.send_header('Content-Type', kind + '; charset=utf-8')
                self.send_header('Content-Length', str(os.fstat(stream.fileno()).st_size))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('Content-Security-Policy',
                    "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                    "connect-src 'self'; img-src data:; base-uri 'none'; frame-ancestors 'none'")
                self.end_headers()
                if not head:
                    while block := stream.read(128 * 1024):
                        self.wfile.write(block)

        def log_message(self, format, *args):
            pass
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', type=Path, required=True)
    parser.add_argument('--port', type=int, default=62772)
    args = parser.parse_args()
    routes = routes_for(args.suite)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler_for(routes))
    print(json.dumps({'url': f'http://127.0.0.1:{server.server_port}/',
                      'routes': sorted(routes)}), flush=True)
    server.serve_forever(poll_interval=.5)


if __name__ == '__main__':
    main()
