import contextlib
import io
import json
import os
import socket
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import itcheck  # noqa: E402


def make_results(local_ip=True, internet=True, dns=True, https=True):
    return [
        {"name": "local_ip", "label": "Local network address", "ok": local_ip, "detail": "x"},
        {"name": "internet_by_ip", "label": "Internet by IP", "ok": internet, "detail": "x"},
        {"name": "dns", "label": "DNS lookup", "ok": dns, "detail": "x"},
        {"name": "https", "label": "HTTPS request", "ok": https, "detail": "x"},
    ]


class FormatTests(unittest.TestCase):
    def test_fmt_bytes_small(self):
        self.assertEqual(itcheck.fmt_bytes(512), "512 B")

    def test_fmt_bytes_gigabytes(self):
        self.assertEqual(itcheck.fmt_bytes(5 * 1024 ** 3), "5.0 GB")


class DiagnoseTests(unittest.TestCase):
    def test_healthy(self):
        self.assertEqual(itcheck.diagnose(make_results()), "Everything looks healthy.")

    def test_no_local_address(self):
        self.assertIn("No local network address",
                      itcheck.diagnose(make_results(local_ip=False, internet=False)))

    def test_no_internet(self):
        self.assertIn("no internet", itcheck.diagnose(make_results(internet=False)))

    def test_dns_failure(self):
        self.assertIn("DNS fails", itcheck.diagnose(make_results(dns=False)))

    def test_website_down(self):
        self.assertIn("did not respond", itcheck.diagnose(make_results(https=False)))


class TcpCheckTests(unittest.TestCase):
    def test_open_port(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        try:
            result = itcheck.tcp_check("127.0.0.1", port, timeout=2)
        finally:
            server.close()
        self.assertTrue(result["ok"])
        self.assertIsNone(result["error"])

    def test_closed_port(self):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()  # nothing is listening on this port now
        result = itcheck.tcp_check("127.0.0.1", port, timeout=2)
        self.assertFalse(result["ok"])
        self.assertIsNotNone(result["error"])


class DiskTests(unittest.TestCase):
    def test_disk_usage_fields(self):
        disk = itcheck.disk_usage(os.path.dirname(os.path.abspath(__file__)))
        self.assertGreater(disk["total"], 0)
        self.assertGreaterEqual(disk["free_percent"], 0)
        self.assertLessEqual(disk["free_percent"], 100)
        self.assertIn("low_space", disk)

    def test_low_space_flag(self):
        fake = mock.Mock(total=1000, used=950, free=50)
        with mock.patch("itcheck.shutil.disk_usage", return_value=fake):
            self.assertTrue(itcheck.disk_usage("/")["low_space"])


class ReportTests(unittest.TestCase):
    def test_markdown_contains_sections(self):
        with mock.patch("itcheck.run_netcheck", return_value=make_results(dns=False)):
            text = itcheck.to_markdown(itcheck.build_report())
        for heading in ("# IT check report", "## System", "## Disk", "## Network"):
            self.assertIn(heading, text)
        self.assertIn("FAIL", text)
        self.assertIn("DNS fails", text)


class CommandLineTests(unittest.TestCase):
    def run_main(self, argv):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = itcheck.main(argv)
        return code, buffer.getvalue()

    def test_sysinfo_json(self):
        code, output = self.run_main(["sysinfo", "--json"])
        self.assertEqual(code, 0)
        self.assertIn("hostname", json.loads(output))

    def test_net_exit_code_reflects_result(self):
        with mock.patch("itcheck.run_netcheck", return_value=make_results(https=False)):
            code, output = self.run_main(["net"])
        self.assertEqual(code, 1)
        self.assertIn("Diagnosis:", output)

    def test_report_writes_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            target = os.path.join(folder, "report.md")
            with mock.patch("itcheck.run_netcheck", return_value=make_results()):
                code, _ = self.run_main(["report", "--output", target])
            self.assertEqual(code, 0)
            with open(target, encoding="utf-8") as handle:
                self.assertIn("# IT check report", handle.read())


if __name__ == "__main__":
    unittest.main()
