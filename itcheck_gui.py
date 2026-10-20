#!/usr/bin/env python3
"""IT Check - a one-window desktop dashboard for this computer.

Double-click it (or ITCheck.exe) and it immediately:
  * shows live CPU, memory, disk and internet-speed tiles with small graphs,
  * runs the 4-step internet check and explains any problem in plain words,
  * re-checks the internet every minute on its own,
  * offers one-click fixes (flush DNS, network settings, disk cleanup, Task Manager),
  * saves a report or copies a summary for a support ticket.

    python itcheck_gui.py

Standard library only (tkinter). All checks come from itcheck.py.
"""

import collections
import datetime
import os
import queue
import subprocess
import sys
import threading

import itcheck

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox
except ImportError:  # pragma: no cover - tkinter ships with Python on Windows
    tk = None

APP_NAME = "IT Check"
STATS_EVERY_MS = 1000       # CPU and memory refresh
PING_EVERY_S = 2.0          # internet latency refresh
NETCHECK_EVERY_MS = 60000   # full internet check refresh
HISTORY = 60                # points kept in each little graph
SLOW_PING_MS = 150
HIGH_USE_PERCENT = 90

IS_WINDOWS = os.name == "nt"
SCALE = 1.0  # set from the screen's DPI when the window opens


def px(value):
    """Scale a size in pixels for high-resolution (4K, 150%+) screens."""
    return int(round(value * SCALE))
FONT = "Segoe UI" if IS_WINDOWS else "DejaVu Sans"

C = {
    "bg": "#f3f5f8", "card": "#ffffff", "line": "#e1e6ec", "text": "#18212b",
    "muted": "#617080", "accent": "#2563eb", "accent_dark": "#1d4ed8",
    "ok": "#15803d", "ok_bg": "#dcfce7", "warn": "#b45309", "warn_bg": "#fef3c7",
    "bad": "#b91c1c", "bad_bg": "#fee2e2", "track": "#e8edf2",
}


# --------------------------------------------------------------------------
# Plain logic (no window needed, so it can be unit tested)
# --------------------------------------------------------------------------

def level_for(percent, warn=75, bad=HIGH_USE_PERCENT):
    """'ok', 'warn' or 'bad' for a usage percentage."""
    if percent is None:
        return "ok"
    if percent >= bad:
        return "bad"
    if percent >= warn:
        return "warn"
    return "ok"


def ping_level(ms):
    if ms is None:
        return "bad"
    return "warn" if ms >= SLOW_PING_MS else "ok"


def find_issues(net_results=None, disk=None, memory=None):
    """List the problems worth telling the user about, most important first."""
    issues = []
    if net_results and not all(item["ok"] for item in net_results):
        issues.append(itcheck.diagnose(net_results))
    if disk and disk.get("low_space"):
        issues.append("Disk %s is almost full (%s%% free)." % (disk["path"], disk["free_percent"]))
    if memory and memory.get("percent", 0) >= HIGH_USE_PERCENT:
        issues.append("Memory is %s%% used. Close programs you are not using." % memory["percent"])
    return issues


def overall_status(issues, checked=True):
    """(level, headline) for the banner at the top of the window."""
    if not checked:
        return "warn", "Checking..."
    if not issues:
        return "ok", "All good"
    return "bad", "1 issue found" if len(issues) == 1 else "%d issues found" % len(issues)


def summary_text(system, disk, memory, cpu, ping_ms, net_results, uptime):
    """A short plain-text summary to paste into a chat or ticket."""
    lines = [
        "IT Check summary - %s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "Computer: %s (%s)" % (system.get("hostname"), system.get("os")),
        "Local IP: %s" % (system.get("local_ip") or "none"),
        "Uptime: %s" % itcheck.fmt_duration(uptime),
        "CPU: %s" % ("%s%%" % cpu if cpu is not None else "unknown"),
    ]
    if memory:
        lines.append("Memory: %s%% used (%s of %s)" % (
            memory["percent"], itcheck.fmt_bytes(memory["used"]), itcheck.fmt_bytes(memory["total"])))
    if disk:
        lines.append("Disk %s: %s free of %s (%s%%)" % (
            disk["path"], itcheck.fmt_bytes(disk["free"]), itcheck.fmt_bytes(disk["total"]),
            disk["free_percent"]))
    lines.append("Internet latency: %s" % ("%s ms" % ping_ms if ping_ms is not None else "no reply"))
    if net_results:
        for item in net_results:
            lines.append("[%s] %s - %s" % ("PASS" if item["ok"] else "FAIL", item["label"], item["detail"]))
    issues = find_issues(net_results, disk, memory)
    lines.append("Result: " + ("; ".join(issues) if issues else "Everything looks healthy."))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Quick fixes (Windows only)
# --------------------------------------------------------------------------

_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW


def flush_dns():
    """Run 'ipconfig /flushdns'. Returns (ok, message)."""
    try:
        done = subprocess.run(["ipconfig", "/flushdns"], capture_output=True, text=True,
                              timeout=15, creationflags=_NO_WINDOW if IS_WINDOWS else 0)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    text = (done.stdout or done.stderr or "").strip().splitlines()
    return done.returncode == 0, text[-1] if text else "Done."


def open_target(target):
    """Open a Windows settings page or start a program."""
    try:
        # startfile goes through the Windows shell, so programs that need
        # administrator rights (like Task Manager) show the normal UAC prompt.
        os.startfile(target)  # noqa: S606 - Windows only, fixed values
        return True, ""
    except (OSError, AttributeError) as exc:
        return False, str(exc)


# --------------------------------------------------------------------------
# Window
# --------------------------------------------------------------------------

class StatTile:
    """A card with a title, a big number, a note and a small live graph."""

    def __init__(self, parent, title, graph=True):
        self.frame = tk.Frame(parent, bg=C["card"], highlightthickness=1,
                              highlightbackground=C["line"], padx=px(16), pady=px(12))
        tk.Label(self.frame, text=title.upper(), bg=C["card"], fg=C["muted"],
                 font=(FONT, 9, "bold")).pack(anchor="w")
        self.value = tk.Label(self.frame, text="--", bg=C["card"], fg=C["text"],
                              font=(FONT, 24, "bold"))
        self.value.pack(anchor="w", pady=(px(2), px(0)))
        self.note = tk.Label(self.frame, text="", bg=C["card"], fg=C["muted"],
                             font=(FONT, 9), anchor="w", justify="left", wraplength=px(200))
        self.note.pack(anchor="w", fill="x")
        self.canvas = tk.Canvas(self.frame, height=px(44 if graph else 14), bg=C["card"],
                                highlightthickness=0)
        self.canvas.pack(fill="x", pady=(px(8), px(0)))
        self.graph = graph
        self.history = collections.deque(maxlen=HISTORY)

    def set(self, value_text, note, level):
        colour = {"ok": C["text"], "warn": C["warn"], "bad": C["bad"]}[level]
        self.value.config(text=value_text, fg=colour)
        self.note.config(text=note)

    def push(self, value, scale_max=100.0, level="ok"):
        self.history.append(value)
        self.draw_graph(scale_max, level)

    def draw_graph(self, scale_max, level):
        cv = self.canvas
        cv.delete("all")
        width = max(cv.winfo_width(), px(120))
        height = int(cv["height"])
        colour = {"ok": C["accent"], "warn": C["warn"], "bad": C["bad"]}[level]
        cv.create_line(0, height - 1, width, height - 1, fill=C["line"])
        points = list(self.history)
        if len(points) < 2:
            return
        top = max(scale_max, max(p for p in points if p is not None) if any(
            p is not None for p in points) else 1)
        step = width / (HISTORY - 1)
        start_x = width - step * (len(points) - 1)
        coords, segments = [], []
        for i, p in enumerate(points):
            if p is None:  # gap where the ping failed
                if len(coords) >= 4:
                    segments.append(coords)
                coords = []
                continue
            coords += [start_x + i * step, height - 3 - (p / top) * (height - 6)]
        if len(coords) >= 4:
            segments.append(coords)
        for seg in segments:
            cv.create_line(*seg, fill=colour, width=max(2, px(2)), smooth=True)

    def draw_bar(self, percent, level):
        cv = self.canvas
        cv.delete("all")
        width = max(cv.winfo_width(), px(120))
        height = int(cv["height"])
        colour = {"ok": C["ok"], "warn": C["warn"], "bad": C["bad"]}[level]
        cv.create_rectangle(0, 2, width, height - 2, fill=C["track"], outline="")
        cv.create_rectangle(0, 2, width * max(0, min(percent, 100)) / 100, height - 2,
                            fill=colour, outline="")


class App:
    def __init__(self, root):
        self.root = root
        self.queue = queue.Queue()
        self.stop = threading.Event()
        self.system = itcheck.sysinfo()
        self.disk = None
        self.memory = None
        self.cpu = None
        self.ping_ms = None
        self.net_results = None
        self.net_running = False
        self.last_cpu = itcheck.cpu_times()

        root.title(APP_NAME)
        root.configure(bg=C["bg"])
        root.geometry("%dx%d" % (px(1040), px(720)))
        root.minsize(px(900), px(640))
        root.protocol("WM_DELETE_WINDOW", self.close)

        self.build()
        self.refresh_stats()
        self.run_netcheck()
        threading.Thread(target=self.ping_loop, daemon=True).start()
        self.root.after(100, self.drain_queue)
        self.root.after(NETCHECK_EVERY_MS, self.auto_netcheck)

    # -- layout -----------------------------------------------------------

    def card(self, parent, title):
        frame = tk.Frame(parent, bg=C["card"], highlightthickness=1,
                         highlightbackground=C["line"], padx=px(18), pady=px(14))
        tk.Label(frame, text=title, bg=C["card"], fg=C["text"],
                 font=(FONT, 12, "bold")).pack(anchor="w", pady=(px(0), px(8)))
        return frame

    def button(self, parent, text, command, primary=False):
        bg = C["accent"] if primary else C["card"]
        fg = "#ffffff" if primary else C["text"]
        btn = tk.Button(parent, text=text, command=command, bg=bg, fg=fg,
                        activebackground=C["accent_dark"] if primary else C["track"],
                        activeforeground=fg, relief="flat", bd=0, cursor="hand2",
                        font=(FONT, 10, "bold" if primary else "normal"), padx=px(14), pady=px(7),
                        highlightthickness=0 if primary else 1,
                        highlightbackground=C["line"])
        return btn

    def build(self):
        outer = tk.Frame(self.root, bg=C["bg"], padx=px(20), pady=px(16))
        outer.pack(fill="both", expand=True)

        # Header
        head = tk.Frame(outer, bg=C["bg"])
        head.pack(fill="x", pady=(px(0), px(14)))
        left = tk.Frame(head, bg=C["bg"])
        left.pack(side="left")
        tk.Label(left, text=APP_NAME, bg=C["bg"], fg=C["text"],
                 font=(FONT, 20, "bold")).pack(anchor="w")
        tk.Label(left, text="%s  ·  %s" % (self.system["hostname"], self.system["os"]),
                 bg=C["bg"], fg=C["muted"], font=(FONT, 10)).pack(anchor="w")
        right = tk.Frame(head, bg=C["bg"])
        right.pack(side="right")
        self.banner = tk.Label(right, text="Checking...", font=(FONT, 12, "bold"),
                               padx=px(16), pady=px(6), bg=C["warn_bg"], fg=C["warn"])
        self.banner.pack(anchor="e")
        self.checked_at = tk.Label(right, text="", bg=C["bg"], fg=C["muted"], font=(FONT, 9))
        self.checked_at.pack(anchor="e", pady=(px(4), px(0)))

        # Live tiles
        tiles = tk.Frame(outer, bg=C["bg"])
        tiles.pack(fill="x")
        self.t_cpu = StatTile(tiles, "CPU")
        self.t_mem = StatTile(tiles, "Memory")
        self.t_disk = StatTile(tiles, "Disk", graph=False)
        self.t_net = StatTile(tiles, "Internet (ping)")
        for i, tile in enumerate((self.t_cpu, self.t_mem, self.t_disk, self.t_net)):
            tile.frame.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else px(12), 0))
            tiles.columnconfigure(i, weight=1, uniform="tiles")

        # Lower area
        lower = tk.Frame(outer, bg=C["bg"])
        lower.pack(fill="both", expand=True, pady=(px(14), px(0)))
        lower.columnconfigure(0, weight=3, uniform="lower")
        lower.columnconfigure(1, weight=2, uniform="lower")
        lower.rowconfigure(0, weight=1)

        # Internet check card
        net = self.card(lower, "Internet check")
        net.grid(row=0, column=0, sticky="nsew", padx=(px(0), px(12)))
        self.steps = []
        for _ in range(4):
            row = tk.Frame(net, bg=C["card"])
            row.pack(fill="x", pady=px(3))
            badge = tk.Label(row, text="  ...  ", bg=C["track"], fg=C["muted"],
                             font=(FONT, 9, "bold"), width=6)
            badge.pack(side="left", padx=(px(0), px(12)))
            texts = tk.Frame(row, bg=C["card"])
            texts.pack(side="left", fill="x", expand=True)
            label = tk.Label(texts, text="", bg=C["card"], fg=C["text"],
                             font=(FONT, 10, "bold"), anchor="w")
            label.pack(anchor="w")
            detail = tk.Label(texts, text="", bg=C["card"], fg=C["muted"],
                              font=(FONT, 9), anchor="w", justify="left", wraplength=px(440))
            detail.pack(anchor="w")
            self.steps.append((badge, label, detail))

        self.diag = tk.Label(net, text="Running the internet check...", bg=C["track"],
                             fg=C["muted"], font=(FONT, 10, "bold"), anchor="w",
                             justify="left", padx=px(12), pady=px(10), wraplength=px(500))
        self.diag.pack(fill="x", pady=(px(12), px(12)))

        controls = tk.Frame(net, bg=C["card"])
        controls.pack(fill="x", side="bottom")
        tk.Label(controls, text="Website to test", bg=C["card"], fg=C["muted"],
                 font=(FONT, 9)).pack(anchor="w")
        entry_row = tk.Frame(controls, bg=C["card"])
        entry_row.pack(fill="x", pady=(px(2), px(0)))
        self.host = tk.StringVar(value="example.com")
        entry = tk.Entry(entry_row, textvariable=self.host, font=(FONT, 10), relief="flat",
                         bg=C["bg"], fg=C["text"], highlightthickness=1,
                         highlightbackground=C["line"], highlightcolor=C["accent"])
        entry.pack(side="left", fill="x", expand=True, ipady=px(6), padx=(px(0), px(8)))
        entry.bind("<Return>", lambda _e: self.run_netcheck())
        self.run_btn = self.button(entry_row, "Run check again", self.run_netcheck, primary=True)
        self.run_btn.pack(side="left")

        # Right column: computer facts + actions
        side = tk.Frame(lower, bg=C["bg"])
        side.grid(row=0, column=1, sticky="nsew")

        info = self.card(side, "This computer")
        info.pack(fill="x")
        grid = tk.Frame(info, bg=C["card"])
        grid.pack(fill="x")
        self.facts = {}
        for r, (key, name) in enumerate((
                ("hostname", "Name"), ("os", "Windows"), ("cpu_cores", "CPU cores"),
                ("local_ip", "Local IP"), ("uptime", "Turned on for"))):
            tk.Label(grid, text=name, bg=C["card"], fg=C["muted"],
                     font=(FONT, 9)).grid(row=r, column=0, sticky="w", pady=px(2), padx=(px(0), px(14)))
            val = tk.Label(grid, text="", bg=C["card"], fg=C["text"], font=(FONT, 10),
                           anchor="w", justify="left", wraplength=px(240))
            val.grid(row=r, column=1, sticky="w", pady=px(2))
            self.facts[key] = val
        if not IS_WINDOWS:
            grid.grid_slaves(row=1, column=0)[0].config(text="System")

        fixes = self.card(side, "Quick fixes")
        fixes.pack(fill="x", pady=(px(12), px(0)))
        fix_grid = tk.Frame(fixes, bg=C["card"])
        fix_grid.pack(fill="x")
        actions = (
            ("Flush DNS", self.do_flush_dns),
            ("Network settings", lambda: self.do_open("ms-settings:network")),
            ("Disk Cleanup", lambda: self.do_open("cleanmgr")),
            ("Task Manager", lambda: self.do_open("taskmgr")),
        )
        for i, (text, cmd) in enumerate(actions):
            btn = self.button(fix_grid, text, cmd)
            btn.grid(row=i // 2, column=i % 2, sticky="ew", padx=(0 if i % 2 == 0 else px(6), 0),
                     pady=(0 if i < 2 else px(6), 0))
            if not IS_WINDOWS:
                btn.config(state="disabled")
        fix_grid.columnconfigure(0, weight=1, uniform="fix")
        fix_grid.columnconfigure(1, weight=1, uniform="fix")

        share = tk.Frame(side, bg=C["bg"])
        share.pack(fill="x", pady=(px(12), px(0)))
        self.button(share, "Save report", self.save_report, primary=True).pack(
            side="left", fill="x", expand=True, padx=(px(0), px(6)))
        self.copy_btn = self.button(share, "Copy summary", self.copy_summary)
        self.copy_btn.pack(side="left", fill="x", expand=True)

        tk.Label(outer, text="Live stats update every second  ·  internet re-checked every minute"
                             "  ·  v%s" % itcheck.__version__,
                 bg=C["bg"], fg=C["muted"], font=(FONT, 9)).pack(side="bottom", pady=(px(12), px(0)))

    # -- background work --------------------------------------------------

    def ping_loop(self):
        while not self.stop.is_set():
            result = itcheck.tcp_check("1.1.1.1", 53, timeout=2.0)
            self.queue.put(("ping", result["ms"] if result["ok"] else None))
            self.stop.wait(PING_EVERY_S)

    def run_netcheck(self):
        if self.net_running:
            return
        site = self.host.get().strip() or "example.com"
        self.net_running = True
        self.run_btn.config(text="Checking...", state="disabled")
        self.diag.config(text="Running the internet check...", bg=C["track"], fg=C["muted"])

        def work():
            try:
                results = itcheck.run_netcheck(site)
            except Exception as exc:  # never let a worker thread die silently
                results = [{"name": "local_ip", "label": "Internet check", "ok": False,
                            "detail": str(exc)}]
            self.queue.put(("net", results))

        threading.Thread(target=work, daemon=True).start()

    def auto_netcheck(self):
        if self.stop.is_set():
            return
        self.run_netcheck()
        self.root.after(NETCHECK_EVERY_MS, self.auto_netcheck)

    def drain_queue(self):
        try:
            while True:
                kind, data = self.queue.get_nowait()
                if kind == "ping":
                    self.show_ping(data)
                elif kind == "net":
                    self.show_net(data)
        except queue.Empty:
            pass
        if not self.stop.is_set():
            self.root.after(100, self.drain_queue)

    # -- updating the screen ----------------------------------------------

    def refresh_stats(self):
        if self.stop.is_set():
            return
        now = itcheck.cpu_times()
        self.cpu = itcheck.cpu_percent(self.last_cpu, now)
        self.last_cpu = now
        if self.cpu is not None:
            lvl = level_for(self.cpu)
            self.t_cpu.set("%d%%" % round(self.cpu), "%s cores" % self.system["cpu_cores"], lvl)
            self.t_cpu.push(self.cpu, 100, lvl)
        else:
            self.t_cpu.set("n/a", "Not available on this system", "ok")

        self.memory = itcheck.memory_info()
        if self.memory:
            lvl = level_for(self.memory["percent"])
            self.t_mem.set("%d%%" % round(self.memory["percent"]),
                           "%s of %s used" % (itcheck.fmt_bytes(self.memory["used"]),
                                              itcheck.fmt_bytes(self.memory["total"])), lvl)
            self.t_mem.push(self.memory["percent"], 100, lvl)
        else:
            self.t_mem.set("n/a", "Not available on this system", "ok")

        self.disk = itcheck.disk_usage()
        used = 100 - self.disk["free_percent"]
        lvl = "bad" if self.disk["low_space"] else ("warn" if self.disk["free_percent"] < 20 else "ok")
        self.t_disk.set(itcheck.fmt_bytes(self.disk["free"]),
                        "free on %s  ·  %s%% of %s" % (self.disk["path"], self.disk["free_percent"],
                                                      itcheck.fmt_bytes(self.disk["total"])), lvl)
        self.t_disk.draw_bar(used, lvl)

        self.facts["hostname"].config(text=self.system["hostname"])
        self.facts["os"].config(text=self.system["os"])
        self.facts["cpu_cores"].config(text=str(self.system["cpu_cores"]))
        self.facts["local_ip"].config(text=itcheck.local_ip() or "none")
        self.facts["uptime"].config(text=itcheck.fmt_duration(itcheck.uptime_seconds()))

        self.update_banner()
        self.root.after(STATS_EVERY_MS, self.refresh_stats)

    def show_ping(self, ms):
        self.ping_ms = ms
        lvl = ping_level(ms)
        if ms is None:
            self.t_net.set("Offline", "No reply from 1.1.1.1", "bad")
        else:
            word = "Fast" if ms < 60 else ("OK" if ms < SLOW_PING_MS else "Slow")
            self.t_net.set("%d ms" % round(ms), "%s  ·  lower is better" % word, lvl)
        self.t_net.push(ms, 100, lvl)

    def show_net(self, results):
        self.net_results = results
        self.net_running = False
        self.run_btn.config(text="Run check again", state="normal")
        for (badge, label, detail), item in zip(self.steps, results):
            if item["ok"]:
                badge.config(text="PASS", bg=C["ok_bg"], fg=C["ok"])
            else:
                badge.config(text="FAIL", bg=C["bad_bg"], fg=C["bad"])
            label.config(text=item["label"])
            detail.config(text=str(item["detail"]))
        healthy = all(item["ok"] for item in results)
        self.diag.config(text=itcheck.diagnose(results),
                         bg=C["ok_bg"] if healthy else C["bad_bg"],
                         fg=C["ok"] if healthy else C["bad"])
        self.checked_at.config(text="Last checked %s" % datetime.datetime.now().strftime("%H:%M:%S"))
        self.update_banner()

    def update_banner(self):
        issues = find_issues(self.net_results, self.disk, self.memory)
        level, text = overall_status(issues, checked=self.net_results is not None)
        colours = {"ok": (C["ok_bg"], C["ok"]), "warn": (C["warn_bg"], C["warn"]),
                   "bad": (C["bad_bg"], C["bad"])}[level]
        self.banner.config(text=("✓  " if level == "ok" else "") + text,
                           bg=colours[0], fg=colours[1])

    # -- buttons ----------------------------------------------------------

    def do_flush_dns(self):
        ok, message = flush_dns()
        (messagebox.showinfo if ok else messagebox.showerror)(APP_NAME, message or "Done.")
        if ok:
            self.run_netcheck()

    def do_open(self, target):
        ok, message = open_target(target)
        if not ok:
            messagebox.showerror(APP_NAME, "Could not open it: %s" % message)

    def save_report(self):
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
        path = filedialog.asksaveasfilename(
            title="Save report", defaultextension=".md",
            initialfile="itcheck-report-%s.md" % stamp,
            filetypes=[("Markdown report", "*.md"), ("Text file", "*.txt")])
        if not path:
            return
        site = self.host.get().strip() or "example.com"
        report = {
            "generated": datetime.datetime.now().isoformat(timespec="seconds"),
            "system": self.system,
            "disk": self.disk or itcheck.disk_usage(),
            "network": self.net_results or itcheck.run_netcheck(site),
        }
        report["diagnosis"] = itcheck.diagnose(report["network"])
        text = itcheck.to_markdown(report)
        text += "\n## Live stats\n\n" + summary_text(
            self.system, self.disk, self.memory, self.cpu, self.ping_ms,
            None, itcheck.uptime_seconds()).split("\n", 1)[1] + "\n"
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
        except OSError as exc:
            messagebox.showerror(APP_NAME, "Could not save the report: %s" % exc)
            return
        messagebox.showinfo(APP_NAME, "Report saved:\n%s" % path)

    def copy_summary(self):
        text = summary_text(self.system, self.disk, self.memory, self.cpu, self.ping_ms,
                            self.net_results, itcheck.uptime_seconds())
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.copy_btn.config(text="Copied ✓")
        self.root.after(1500, lambda: self.copy_btn.config(text="Copy summary"))

    def close(self):
        self.stop.set()
        self.root.destroy()


def main():
    if tk is None:
        print("This app needs tkinter, which comes with the standard Python installer from python.org.")
        return 1
    if IS_WINDOWS:
        try:  # sharp text on high-DPI screens
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (OSError, AttributeError):
            pass
    root = tk.Tk()
    global SCALE
    SCALE = max(1.0, root.winfo_fpixels("1i") / 96.0)
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
