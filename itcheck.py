#!/usr/bin/env python3
"""itcheck - quick IT support diagnostics using only the Python standard library.

Commands:
    sysinfo   Show basic details about this computer.
    disk      Show disk usage and warn when space is low.
    net       Run a step-by-step internet check and say where the problem is.
    report    Combine everything into a Markdown report you can attach to a ticket.

Examples:
    python itcheck.py sysinfo
    python itcheck.py net --host example.com
    python itcheck.py report --output report.md
"""

import argparse
import datetime
import json
import os
import platform
import shutil
import socket
import sys
import time
import urllib.error
import urllib.request

__version__ = "0.2.0"

LOW_DISK_PERCENT = 10.0  # warn when less than this share of the disk is free


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def fmt_bytes(num):
    """Turn a byte count into a short human-readable string."""
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return "%d B" % int(value)
            return "%.1f %s" % (value, unit)
        value /= 1024


def _elapsed_ms(start):
    return round((time.perf_counter() - start) * 1000, 1)


# --------------------------------------------------------------------------
# System checks
# --------------------------------------------------------------------------

def local_ip():
    """Return this computer's main local IP address, or None if it has none.

    Connecting a UDP socket does not send any data; it only asks the operating
    system which network interface it would use.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def sysinfo():
    """Collect basic facts about this computer."""
    return {
        "hostname": socket.gethostname(),
        "os": platform.platform(),
        "architecture": platform.machine(),
        "cpu_cores": os.cpu_count(),
        "python": platform.python_version(),
        "local_ip": local_ip(),
    }


# --------------------------------------------------------------------------
# Live stats: CPU, memory, uptime (Windows uses the Win32 API, Linux /proc)
# --------------------------------------------------------------------------

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    class _FILETIME(ctypes.Structure):
        _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    class _MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]


def cpu_times():
    """Return (idle, total) CPU time counters, or None if unavailable.

    Call it twice and compare the two results with cpu_percent().
    """
    try:
        if os.name == "nt":
            idle, kernel, user = _FILETIME(), _FILETIME(), _FILETIME()
            if not ctypes.windll.kernel32.GetSystemTimes(
                    ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
                return None
            as_int = lambda ft: (ft.high << 32) | ft.low  # noqa: E731
            # Kernel time already includes idle time on Windows.
            return as_int(idle), as_int(kernel) + as_int(user)
        with open("/proc/stat") as handle:
            fields = [int(x) for x in handle.readline().split()[1:]]
        idle = fields[3] + (fields[4] if len(fields) > 4 else 0)  # idle + iowait
        return idle, sum(fields[:8])
    except (OSError, ValueError, AttributeError, IndexError):
        return None


def cpu_percent(before, after):
    """Percent of CPU in use between two cpu_times() samples."""
    if not before or not after:
        return None
    total = after[1] - before[1]
    if total <= 0:
        return 0.0
    busy = total - (after[0] - before[0])
    return round(max(0.0, min(100.0, busy / total * 100)), 1)


def memory_info():
    """Return RAM totals as {'total', 'used', 'available', 'percent'}, or None."""
    try:
        if os.name == "nt":
            status = _MEMORYSTATUSEX()
            status.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return None
            total, available = status.ullTotalPhys, status.ullAvailPhys
        else:
            values = {}
            with open("/proc/meminfo") as handle:
                for line in handle:
                    key, _, rest = line.partition(":")
                    values[key] = int(rest.split()[0]) * 1024
            total = values["MemTotal"]
            available = values.get("MemAvailable", values.get("MemFree", 0))
    except (OSError, ValueError, KeyError, AttributeError, IndexError):
        return None
    used = total - available
    return {"total": total, "used": used, "available": available,
            "percent": round(used / total * 100, 1) if total else 0.0}


def uptime_seconds():
    """Seconds since the computer started, or None if unknown."""
    try:
        if os.name == "nt":
            kernel32 = ctypes.windll.kernel32
            kernel32.GetTickCount64.restype = ctypes.c_ulonglong
            return int(kernel32.GetTickCount64() // 1000)
        with open("/proc/uptime") as handle:
            return int(float(handle.read().split()[0]))
    except (OSError, ValueError, AttributeError, IndexError):
        return None


def fmt_duration(seconds):
    """Turn seconds into '3d 4h', '5h 12m' or '7m'."""
    if seconds is None:
        return "unknown"
    days, rest = divmod(int(seconds), 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return "%dd %dh" % (days, hours)
    if hours:
        return "%dh %dm" % (hours, minutes)
    return "%dm" % minutes


def disk_usage(path=None):
    """Return disk usage for the drive that holds `path` (default: current drive)."""
    if path is None:
        path = os.path.abspath(os.sep)
    usage = shutil.disk_usage(path)
    free_percent = round(usage.free / usage.total * 100, 1) if usage.total else 0.0
    return {
        "path": path,
        "total": usage.total,
        "used": usage.used,
        "free": usage.free,
        "free_percent": free_percent,
        "low_space": free_percent < LOW_DISK_PERCENT,
    }


# --------------------------------------------------------------------------
# Network checks
# --------------------------------------------------------------------------

def tcp_check(host, port, timeout=3.0):
    """Try to open a TCP connection to host:port."""
    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
        return {"ok": True, "ms": _elapsed_ms(start), "error": None}
    except OSError as exc:
        return {"ok": False, "ms": None, "error": str(exc)}


def dns_check(host):
    """Try to turn a host name into IP addresses."""
    start = time.perf_counter()
    try:
        infos = socket.getaddrinfo(host, None)
        addresses = sorted({info[4][0] for info in infos})
        return {"ok": True, "ms": _elapsed_ms(start), "addresses": addresses, "error": None}
    except socket.gaierror as exc:
        return {"ok": False, "ms": None, "addresses": [], "error": str(exc)}


def http_check(url, timeout=5.0):
    """Request a web page and report the HTTP status."""
    request = urllib.request.Request(url, headers={"User-Agent": "itcheck/" + __version__})
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            return {"ok": 200 <= status < 400, "status": status,
                    "ms": _elapsed_ms(start), "error": None}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "ms": None, "error": str(exc)}
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return {"ok": False, "status": None, "ms": None, "error": str(reason)}


def run_netcheck(host="example.com", timeout=3.0):
    """Run the checks in the same order a technician would.

    1. Does this computer have a local network address?
    2. Can it reach the internet by IP address (no DNS needed)?
    3. Can it turn a name into an IP address (DNS)?
    4. Does a real website answer over HTTPS?
    """
    results = []

    ip = local_ip()
    results.append({
        "name": "local_ip",
        "label": "Local network address",
        "ok": bool(ip) and not ip.startswith("127."),
        "detail": ip or "none found",
    })

    reach = tcp_check("1.1.1.1", 53, timeout)
    results.append({
        "name": "internet_by_ip",
        "label": "Internet by IP (1.1.1.1:53)",
        "ok": reach["ok"],
        "detail": ("%s ms" % reach["ms"]) if reach["ok"] else reach["error"],
    })

    dns = dns_check(host)
    results.append({
        "name": "dns",
        "label": "DNS lookup (%s)" % host,
        "ok": dns["ok"],
        "detail": ", ".join(dns["addresses"][:3]) if dns["ok"] else dns["error"],
    })

    web = http_check("https://%s" % host, timeout=timeout + 2)
    results.append({
        "name": "https",
        "label": "HTTPS request (%s)" % host,
        "ok": web["ok"],
        "detail": ("HTTP %s in %s ms" % (web["status"], web["ms"])) if web["ok"] else web["error"],
    })

    return results


def diagnose(results):
    """Turn the list of check results into one plain-language conclusion."""
    ok = {item["name"]: item["ok"] for item in results}
    if not ok.get("local_ip"):
        return ("No local network address. Check the cable, the Wi-Fi connection, "
                "or the network adapter.")
    if not ok.get("internet_by_ip"):
        return ("Connected locally but no internet. Check the router, the modem, "
                "or the internet provider.")
    if not ok.get("dns"):
        return ("Internet works by IP address but DNS fails. Try another DNS server "
                "(for example 1.1.1.1) or flush the DNS cache.")
    if not ok.get("https"):
        return ("DNS works but the website did not respond. The site may be down, "
                "or a firewall or proxy may be blocking it.")
    return "Everything looks healthy."


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

def build_report(host="example.com", path=None):
    """Gather every check into one dictionary."""
    network = run_netcheck(host)
    return {
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "system": sysinfo(),
        "disk": disk_usage(path),
        "network": network,
        "diagnosis": diagnose(network),
    }


def to_markdown(report):
    """Render a report dictionary as Markdown."""
    system = report["system"]
    disk = report["disk"]
    lines = [
        "# IT check report",
        "",
        "Generated: %s" % report["generated"],
        "",
        "## System",
        "",
        "- Hostname: %s" % system["hostname"],
        "- OS: %s" % system["os"],
        "- Architecture: %s" % system["architecture"],
        "- CPU cores: %s" % system["cpu_cores"],
        "- Local IP: %s" % (system["local_ip"] or "none"),
        "",
        "## Disk",
        "",
        "- Drive: %s" % disk["path"],
        "- Total: %s" % fmt_bytes(disk["total"]),
        "- Used: %s" % fmt_bytes(disk["used"]),
        "- Free: %s (%s%%)" % (fmt_bytes(disk["free"]), disk["free_percent"]),
        "- Status: %s" % ("LOW SPACE" if disk["low_space"] else "OK"),
        "",
        "## Network",
        "",
        "| Check | Result | Detail |",
        "| --- | --- | --- |",
    ]
    for item in report["network"]:
        lines.append("| %s | %s | %s |" % (
            item["label"], "PASS" if item["ok"] else "FAIL", item["detail"]))
    lines += ["", "**Diagnosis:** %s" % report["diagnosis"], ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

def _print_sysinfo(info):
    for key, value in info.items():
        print("%-14s %s" % (key.replace("_", " ") + ":", value))


def _print_disk(disk):
    print("Drive:  %s" % disk["path"])
    print("Total:  %s" % fmt_bytes(disk["total"]))
    print("Used:   %s" % fmt_bytes(disk["used"]))
    print("Free:   %s (%s%%)" % (fmt_bytes(disk["free"]), disk["free_percent"]))
    if disk["low_space"]:
        print("WARNING: less than %d%% of the disk is free." % LOW_DISK_PERCENT)


def _print_net(results):
    for item in results:
        print("[%s] %-32s %s" % ("PASS" if item["ok"] else "FAIL", item["label"], item["detail"]))
    print()
    print("Diagnosis: %s" % diagnose(results))


def build_parser():
    parser = argparse.ArgumentParser(
        prog="itcheck", description="Quick IT support diagnostics (standard library only).")
    parser.add_argument("--version", action="version", version="itcheck " + __version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_sys = sub.add_parser("sysinfo", help="show basic computer details")
    p_sys.add_argument("--json", action="store_true", help="print JSON instead of text")

    p_disk = sub.add_parser("disk", help="show disk usage")
    p_disk.add_argument("--path", default=None, help="folder or drive to check")
    p_disk.add_argument("--json", action="store_true", help="print JSON instead of text")

    p_net = sub.add_parser("net", help="step-by-step internet check")
    p_net.add_argument("--host", default="example.com", help="site to test (default: example.com)")
    p_net.add_argument("--json", action="store_true", help="print JSON instead of text")

    p_rep = sub.add_parser("report", help="full Markdown report")
    p_rep.add_argument("--host", default="example.com", help="site to test (default: example.com)")
    p_rep.add_argument("--output", default=None, help="write the report to this file")

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.command == "sysinfo":
        info = sysinfo()
        if args.json:
            print(json.dumps(info, indent=2))
        else:
            _print_sysinfo(info)
        return 0

    if args.command == "disk":
        disk = disk_usage(args.path)
        if args.json:
            print(json.dumps(disk, indent=2))
        else:
            _print_disk(disk)
        return 1 if disk["low_space"] else 0

    if args.command == "net":
        results = run_netcheck(args.host)
        if args.json:
            print(json.dumps({"checks": results, "diagnosis": diagnose(results)}, indent=2))
        else:
            _print_net(results)
        return 0 if all(item["ok"] for item in results) else 1

    if args.command == "report":
        text = to_markdown(build_report(args.host))
        if args.output:
            with open(args.output, "w", encoding="utf-8") as handle:
                handle.write(text)
            print("Report written to %s" % args.output)
        else:
            print(text)
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
