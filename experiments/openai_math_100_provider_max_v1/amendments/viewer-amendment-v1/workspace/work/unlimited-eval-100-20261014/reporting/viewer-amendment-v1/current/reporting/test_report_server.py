"""The local report viewer exposes selected artifacts, never raw run files."""
import http.client
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest

from serve_comparison import MODELS, REPORT_FILES, OPTIONAL_SUITE_FILES, handler_for, routes_for


class ReportServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'suite'
        self.root.mkdir()
        names = ['index.html', 'comparison-data.json', 'questions.html', 'questions-data.json']
        names += [f'report-{model}/{name}' for model in MODELS for name in REPORT_FILES]
        for name in names:
            path = self.root / name
            path.parent.mkdir(exist_ok=True)
            path.write_text('synthetic local viewer fixture')

    def test_only_allowlisted_routes_and_loopback_hosts_are_served(self):
        routes = routes_for(self.root)
        private = self.root / f'report-{MODELS[0]}/all_scheduled_attempts.json'
        private.write_text('PRIVATE RESPONSE')
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(routes))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def get(path, method='GET', host=None):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
            connection.request(method, path, headers={'Host': host} if host else {})
            response = connection.getresponse()
            result = response.status, response.read(), dict(response.getheaders())
            connection.close()
            return result

        for route in routes:
            with self.subTest(route=route):
                status, body, headers = get(route)
                self.assertEqual(status, 200)
                self.assertEqual(body, b'synthetic local viewer fixture')
                self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        for route in [f'/report-{MODELS[0]}/all_scheduled_attempts.json', '/',
                      '/status.json', '/../secret', '/%2e%2e/secret', f'/report-{MODELS[0]}/']:
            with self.subTest(private_route=route):
                status, body, _ = get(route, host='untrusted.example' if route == '/' else None)
                self.assertIn(status, (403, 404))
                self.assertNotIn(b'PRIVATE RESPONSE', body)
        self.assertEqual(get('/index.html', method='HEAD')[1], b'')
        self.assertEqual(get('/index.html?view=costs')[0], 200)
        # A target replaced with a symlink after startup must not expose its new contents.
        (self.root / 'index.html').unlink()
        (self.root / 'index.html').symlink_to(private)
        self.assertEqual(get('/index.html')[0], 404)

    def test_missing_or_linked_artifact_prevents_startup(self):
        path = self.root / 'questions.html'
        path.unlink()
        with self.assertRaises(ValueError):
            routes_for(self.root)
        path.symlink_to(self.root / 'index.html')
        with self.assertRaises(ValueError):
            routes_for(self.root)

    def test_optional_disclosures_are_exactly_allowlisted(self):
        for name in OPTIONAL_SUITE_FILES:
            (self.root/name).write_text('{}')
        (self.root/'private-stream.sse').write_text('PRIVATE')
        (self.root/'root-review.json').write_text('PRIVATE')
        routes=routes_for(self.root)
        for name in OPTIONAL_SUITE_FILES:self.assertIn('/'+name,routes)
        self.assertNotIn('/private-stream.sse',routes)
        self.assertNotIn('/root-review.json',routes)
        path=self.root/OPTIONAL_SUITE_FILES[0]
        path.unlink();path.symlink_to(self.root/'root-review.json')
        with self.assertRaises(ValueError):routes_for(self.root)


if __name__ == '__main__':
    unittest.main()
