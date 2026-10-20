import contextlib
import http.client
import io
import json
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import itcheck  # noqa: E402
import itcheck_app  # noqa: E402


def fake_checks(https_ok=True):
    return [
        {"name": "local_ip", "label": "Local network address", "ok": True, "detail": "192.168.1.5"},
        {"name": "internet_by_ip", "label": "Internet by IP", "ok": True, "detail": "12.0 ms"},
        {"name": "dns", "label": "DNS lookup", "ok": True, "detail": "93.184.216.34"},
        {"name": "https", "label": "HTTPS request", "ok": https_ok,
         "detail": "HTTP 200" if https_ok else "timed out"},
    ]


class ValidHostTests(unittest.TestCase):
    def test_accepts_normal_names(self):
        for name in ("example.com", "google.com", "1.1.1.1", "a", "my-site.co.uk"):
            self.assertTrue(itcheck_app.valid_host(name), name)

    def test_rejects_bad_names(self):
        for name in ("", "bad host", "a/b", "-start.com", "end.com-", "x..y", "a;b", "http://x.com",
                     "a" * 300):
            self.assertFalse(itcheck_app.valid_host(name), name)


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = itcheck_app.make_server(0)  # port 0 = any free port
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def call(self, method, path, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, headers=headers or {})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read().decode("utf-8")
        finally:
            conn.close()

    def test_home_page(self):
        status, headers, body = self.call("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn("IT Check", body)
        self.assertIn(itcheck.__version__, body)
        self.assertNotIn("__VERSION__", body)

    def test_system_endpoint(self):
        status, _, body = self.call("GET", "/api/system")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertIn("hostname", data["system"])
        self.assertIn("free_percent", data["disk"])

    def test_net_endpoint_healthy(self):
        with mock.patch("itcheck.run_netcheck", return_value=fake_checks()):
            status, _, body = self.call("GET", "/api/net?host=example.com")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertTrue(data["healthy"])
        self.assertEqual(data["diagnosis"], "Everything looks healthy.")
        self.assertEqual(len(data["checks"]), 4)

    def test_net_endpoint_reports_failure(self):
        with mock.patch("itcheck.run_netcheck", return_value=fake_checks(https_ok=False)):
            _, _, body = self.call("GET", "/api/net")
        data = json.loads(body)
        self.assertFalse(data["healthy"])
        self.assertIn("did not respond", data["diagnosis"])

    def test_net_endpoint_rejects_bad_host(self):
        status, _, body = self.call("GET", "/api/net?host=bad%20host%21")
        self.assertEqual(status, 400)
        self.assertIn("error", json.loads(body))

    def test_report_download(self):
        with mock.patch("itcheck.run_netcheck", return_value=fake_checks()):
            status, headers, body = self.call("GET", "/api/report?host=example.com")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertIn(".md", headers["Content-Disposition"])
        self.assertIn("# IT check report", body)

    def test_report_rejects_bad_host(self):
        status, _, _ = self.call("GET", "/api/report?host=a%2Fb")
        self.assertEqual(status, 400)

    def test_foreign_host_header_is_refused(self):
        status, _, _ = self.call("GET", "/api/system", headers={"Host": "evil.example.com"})
        self.assertEqual(status, 403)

    def test_localhost_host_header_is_allowed(self):
        status, _, _ = self.call("GET", "/api/system", headers={"Host": "localhost:%d" % self.port})
        self.assertEqual(status, 200)

    def test_unknown_path(self):
        status, _, _ = self.call("GET", "/nope")
        self.assertEqual(status, 404)

    def test_quit_needs_the_header(self):
        status, _, _ = self.call("POST", "/api/quit")
        self.assertEqual(status, 403)


class QuitTests(unittest.TestCase):
    def test_quit_stops_the_server(self):
        server = itcheck_app.make_server(0)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("POST", "/api/quit", headers={"X-ITCheck": "1"})
        self.assertEqual(conn.getresponse().status, 200)
        conn.close()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        server.server_close()


class StartupTests(unittest.TestCase):
    def test_port_in_use_returns_error(self):
        first = itcheck_app.make_server(0)
        port = first.server_address[1]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                code = itcheck_app.main(["--port", str(port), "--no-browser"])
        finally:
            first.server_close()
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
