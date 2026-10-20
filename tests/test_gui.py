import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import itcheck  # noqa: E402
import itcheck_gui as gui  # noqa: E402  (imports fine even without a display)


def checks(all_ok=True):
    return [
        {"name": "local_ip", "label": "Local network address", "ok": True, "detail": "192.168.1.5"},
        {"name": "internet_by_ip", "label": "Internet by IP", "ok": all_ok, "detail": "12 ms"},
        {"name": "dns", "label": "DNS lookup", "ok": all_ok, "detail": "93.184.216.34"},
        {"name": "https", "label": "HTTPS request", "ok": all_ok, "detail": "HTTP 200"},
    ]


DISK_OK = {"path": "C:\\", "total": 500e9, "used": 200e9, "free": 300e9,
           "free_percent": 60.0, "low_space": False}
DISK_LOW = dict(DISK_OK, free=20e9, free_percent=4.0, low_space=True)
MEM_OK = {"total": 16e9, "used": 8e9, "available": 8e9, "percent": 50.0}
MEM_HIGH = dict(MEM_OK, percent=95.0)


class LevelTests(unittest.TestCase):
    def test_usage_levels(self):
        self.assertEqual(gui.level_for(10), "ok")
        self.assertEqual(gui.level_for(80), "warn")
        self.assertEqual(gui.level_for(95), "bad")
        self.assertEqual(gui.level_for(None), "ok")

    def test_ping_levels(self):
        self.assertEqual(gui.ping_level(20), "ok")
        self.assertEqual(gui.ping_level(400), "warn")
        self.assertEqual(gui.ping_level(None), "bad")


class IssueTests(unittest.TestCase):
    def test_healthy_computer_has_no_issues(self):
        issues = gui.find_issues(checks(), DISK_OK, MEM_OK)
        self.assertEqual(issues, [])
        self.assertEqual(gui.overall_status(issues), ("ok", "All good"))

    def test_every_problem_is_listed(self):
        issues = gui.find_issues(checks(all_ok=False), DISK_LOW, MEM_HIGH)
        self.assertEqual(len(issues), 3)
        self.assertIn("no internet", issues[0])
        self.assertIn("almost full", issues[1])
        self.assertIn("Memory", issues[2])
        self.assertEqual(gui.overall_status(issues), ("bad", "3 issues found"))

    def test_single_issue_wording(self):
        self.assertEqual(gui.overall_status(["x"]), ("bad", "1 issue found"))

    def test_before_first_check(self):
        self.assertEqual(gui.overall_status([], checked=False)[0], "warn")


class SummaryTests(unittest.TestCase):
    def test_summary_mentions_the_key_facts(self):
        system = {"hostname": "FRONT-DESK", "os": "Windows-11", "local_ip": "192.168.1.5"}
        text = gui.summary_text(system, DISK_OK, MEM_OK, 12.5, 23.0, checks(), 3700)
        for part in ("FRONT-DESK", "192.168.1.5", "1h 1m", "CPU: 12.5%", "Memory: 50.0%",
                     "23.0 ms", "[PASS] HTTPS request", "Everything looks healthy."):
            self.assertIn(part, text)

    def test_summary_when_offline(self):
        system = {"hostname": "PC", "os": "Windows", "local_ip": None}
        text = gui.summary_text(system, DISK_LOW, None, None, None, checks(all_ok=False), None)
        self.assertIn("no reply", text)
        self.assertIn("Local IP: none", text)
        self.assertIn("almost full", text)


class LiveStatsTests(unittest.TestCase):
    def test_cpu_percent_math(self):
        self.assertEqual(itcheck.cpu_percent((100, 1000), (150, 1100)), 50.0)
        self.assertEqual(itcheck.cpu_percent((100, 1000), (100, 1000)), 0.0)
        self.assertIsNone(itcheck.cpu_percent(None, (1, 2)))

    def test_cpu_times_and_memory_on_this_machine(self):
        if not (os.name == "nt" or sys.platform.startswith("linux")):
            self.skipTest("live stats only implemented for Windows and Linux")
        before = itcheck.cpu_times()
        after = itcheck.cpu_times()
        percent = itcheck.cpu_percent(before, after)
        self.assertTrue(0 <= percent <= 100)
        memory = itcheck.memory_info()
        self.assertGreater(memory["total"], 0)
        self.assertTrue(0 <= memory["percent"] <= 100)
        self.assertGreaterEqual(itcheck.uptime_seconds(), 0)

    def test_fmt_duration(self):
        self.assertEqual(itcheck.fmt_duration(59), "0m")
        self.assertEqual(itcheck.fmt_duration(3 * 3600 + 5 * 60), "3h 5m")
        self.assertEqual(itcheck.fmt_duration(2 * 86400 + 7200), "2d 2h")
        self.assertEqual(itcheck.fmt_duration(None), "unknown")


if __name__ == "__main__":
    unittest.main()
