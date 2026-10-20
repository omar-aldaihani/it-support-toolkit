#!/usr/bin/env python3
"""IT Check app - a small local app with buttons and colored results.

Start it, and it opens a page in your web browser. The page shows system
details, disk space, and an internet check, and lets you download a report.

    python itcheck_app.py

The app only listens on this computer (127.0.0.1), so nobody else on the
network can open it. It uses only the Python standard library and reuses the
checks from itcheck.py.
"""

import argparse
import datetime
import http.server
import json
import os
import re
import sys
import threading
import urllib.parse
import webbrowser

import itcheck

DEFAULT_PORT = 8765
DEFAULT_SITE = "example.com"

# Letters, digits, dots, and dashes only; no leading or trailing dot or dash.
_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")


def valid_host(value):
    """True when `value` looks like a plain host name or IP address."""
    return bool(value) and ".." not in value and bool(_HOST_RE.match(value))


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>IT Check</title>
<style>
:root {
  --bg: #f4f6f8; --card: #ffffff; --text: #1d2733; --muted: #5f6b7a;
  --line: #dfe4ea; --accent: #1f6feb; --accent-text: #ffffff;
  --ok: #1a7f37; --ok-bg: #dafbe1; --bad: #cf222e; --bad-bg: #ffebe9;
  --warn: #9a6700; --warn-bg: #fff8c5;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0f141a; --card: #18202a; --text: #e6edf3; --muted: #9aa7b5;
    --line: #2b3644; --accent: #4c9aff; --accent-text: #0b1118;
    --ok: #56d364; --ok-bg: #12361f; --bad: #ff7b72; --bad-bg: #3d1519;
    --warn: #e3b341; --warn-bg: #3a2e0c;
  }
}
* { box-sizing: border-box; }
[hidden] { display: none !important; }
body {
  margin: 0; padding: 24px 16px 40px; background: var(--bg); color: var(--text);
  font: 16px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
main { max-width: 760px; margin: 0 auto; }
header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 16px; }
h1 { margin: 0; font-size: 28px; }
h2 { margin: 0 0 12px; font-size: 18px; }
.sub { margin: 2px 0 0; color: var(--muted); }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 16px 18px; margin-bottom: 14px; }
.muted { color: var(--muted); }
.facts { display: grid; grid-template-columns: max-content 1fr; gap: 6px 18px; margin: 0; }
.facts dt { color: var(--muted); }
.facts dd { margin: 0; word-break: break-word; }
.bar { height: 14px; background: var(--line); border-radius: 7px; overflow: hidden; margin-bottom: 8px; }
#barfill { height: 100%; width: 0; background: var(--ok); transition: width 0.4s; }
#barfill.warn { background: var(--warn); }
#barfill.bad { background: var(--bad); }
.row { display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
input[type=text] {
  flex: 1 1 200px; min-width: 0; padding: 9px 12px; font: inherit; color: var(--text);
  background: var(--bg); border: 1px solid var(--line); border-radius: 8px;
}
button, .btn {
  font: inherit; padding: 9px 16px; border-radius: 8px; border: 1px solid var(--line);
  background: var(--card); color: var(--text); cursor: pointer; text-decoration: none; display: inline-block;
}
button.primary { background: var(--accent); border-color: var(--accent); color: var(--accent-text); font-weight: 600; }
button:disabled { opacity: 0.6; cursor: default; }
.checks { list-style: none; margin: 0 0 12px; padding: 0; }
.checks li { display: flex; align-items: baseline; gap: 12px; padding: 8px 0; border-bottom: 1px solid var(--line); }
.checks li:last-child { border-bottom: 0; }
.badge { flex: none; min-width: 52px; text-align: center; font-size: 13px; font-weight: 700; padding: 2px 8px; border-radius: 6px; }
.badge.pass { color: var(--ok); background: var(--ok-bg); }
.badge.fail { color: var(--bad); background: var(--bad-bg); }
.check-label { font-weight: 600; }
.check-detail { color: var(--muted); font-size: 14px; word-break: break-word; }
.note { padding: 10px 14px; border-radius: 8px; margin: 0 0 12px; font-weight: 600; }
.note.ok { color: var(--ok); background: var(--ok-bg); }
.note.bad { color: var(--bad); background: var(--bad-bg); }
.note.warn { color: var(--warn); background: var(--warn-bg); }
.sr { position: absolute; left: -9999px; }
footer { color: var(--muted); font-size: 13px; text-align: center; margin-top: 20px; }
@media (max-width: 480px) { .facts { grid-template-columns: 1fr; gap: 0; } .facts dd { margin-bottom: 8px; } }
</style>
</head>
<body>
<main>
  <header>
    <div>
      <h1>IT Check</h1>
      <p class="sub">Quick diagnostics for this computer</p>
    </div>
    <button id="quit" type="button">Quit</button>
  </header>

  <noscript><p class="note bad">This page needs JavaScript turned on.</p></noscript>

  <section class="card">
    <h2>This computer</h2>
    <dl id="sys" class="facts"><dt>Loading</dt><dd>...</dd></dl>
  </section>

  <section class="card">
    <h2>Disk space</h2>
    <div class="bar" id="bar" role="progressbar" aria-label="Disk used" aria-valuemin="0" aria-valuemax="100"><div id="barfill"></div></div>
    <p id="disktext" class="muted">Loading...</p>
    <p id="diskwarn" class="note warn" hidden>Low disk space. Free some space, because a nearly full drive makes the computer slow.</p>
  </section>

  <section class="card">
    <h2>Internet check</h2>
    <form id="netform" class="row">
      <label for="host" class="sr">Website to test</label>
      <input id="host" type="text" value="example.com" maxlength="253" autocomplete="off" spellcheck="false">
      <button id="run" type="submit" class="primary">Run check</button>
    </form>
    <ul id="checks" class="checks"></ul>
    <p id="diag" class="note" hidden></p>
    <a id="report" class="btn" href="/api/report">Download report</a>
    <span class="muted"> Saves a text report you can attach to a support ticket.</span>
  </section>

  <footer>IT Check v__VERSION__ &middot; runs only on this computer</footer>
</main>

<script>
(function () {
  "use strict";

  function $(id) { return document.getElementById(id); }

  function make(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) { node.className = cls; }
    if (text !== undefined) { node.textContent = text; }
    return node;
  }

  function fmt(n) {
    var units = ["B", "KB", "MB", "GB", "TB"], i = 0;
    while (n >= 1024 && i < units.length - 1) { n = n / 1024; i += 1; }
    return (i === 0 ? n.toFixed(0) : n.toFixed(1)) + " " + units[i];
  }

  function request(method, url, cb) {
    var xhr = new XMLHttpRequest();
    xhr.open(method, url);
    if (method === "POST") { xhr.setRequestHeader("X-ITCheck", "1"); }
    xhr.onload = function () {
      var data = null;
      try { data = JSON.parse(xhr.responseText); } catch (e) { data = null; }
      if (xhr.status >= 200 && xhr.status < 300 && data) { cb(null, data); }
      else { cb(new Error((data && data.error) || ("Request failed (" + xhr.status + ")"))); }
    };
    xhr.onerror = function () { cb(new Error("Could not reach the app. Is it still running?")); };
    xhr.send();
  }

  var LABELS = [
    ["hostname", "Computer name"], ["os", "Operating system"], ["architecture", "Architecture"],
    ["cpu_cores", "CPU cores"], ["local_ip", "Local IP address"], ["python", "Python"]
  ];

  function renderSystem(info) {
    var list = $("sys");
    list.textContent = "";
    LABELS.forEach(function (pair) {
      var value = info[pair[0]];
      list.appendChild(make("dt", "", pair[1]));
      list.appendChild(make("dd", "", value === null || value === undefined ? "none" : String(value)));
    });
  }

  function renderDisk(disk) {
    var usedPct = disk.total ? Math.round(disk.used / disk.total * 1000) / 10 : 0;
    var fill = $("barfill");
    fill.style.width = usedPct + "%";
    fill.className = disk.free_percent < 10 ? "bad" : (disk.free_percent < 20 ? "warn" : "");
    $("bar").setAttribute("aria-valuenow", String(usedPct));
    $("disktext").textContent = "Drive " + disk.path + ": " + fmt(disk.free) + " free of " +
      fmt(disk.total) + " (" + disk.free_percent + "% free)";
    $("diskwarn").hidden = !disk.low_space;
  }

  function showDiagnosis(text, good) {
    var box = $("diag");
    box.textContent = text;
    box.className = "note " + (good ? "ok" : "bad");
    box.hidden = false;
  }

  function renderNet(data) {
    var list = $("checks");
    list.textContent = "";
    data.checks.forEach(function (item) {
      var row = make("li");
      row.appendChild(make("span", "badge " + (item.ok ? "pass" : "fail"), item.ok ? "PASS" : "FAIL"));
      var body = make("div");
      body.appendChild(make("div", "check-label", item.label));
      body.appendChild(make("div", "check-detail", String(item.detail)));
      row.appendChild(body);
      list.appendChild(row);
    });
    showDiagnosis(data.diagnosis, data.healthy);
  }

  function runNet() {
    var host = $("host").value.trim() || "example.com";
    var button = $("run");
    button.disabled = true;
    button.textContent = "Checking...";
    $("checks").textContent = "";
    $("diag").hidden = true;
    $("report").href = "/api/report?host=" + encodeURIComponent(host);
    request("GET", "/api/net?host=" + encodeURIComponent(host), function (err, data) {
      button.disabled = false;
      button.textContent = "Run check";
      if (err) { showDiagnosis(err.message, false); return; }
      renderNet(data);
    });
  }

  $("netform").addEventListener("submit", function (event) {
    event.preventDefault();
    runNet();
  });

  $("quit").addEventListener("click", function () {
    request("POST", "/api/quit", function () {
      document.body.textContent = "IT Check has stopped. You can close this tab.";
    });
  });

  request("GET", "/api/system", function (err, data) {
    if (err) {
      $("sys").textContent = err.message;
      return;
    }
    renderSystem(data.system);
    renderDisk(data.disk);
  });
  runNet();
})();
</script>
</body>
</html>
"""


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "ITCheck/" + itcheck.__version__

    def log_message(self, fmt, *args):  # keep the console quiet
        pass

    # -- helpers ----------------------------------------------------------

    def _send(self, status, body, content_type, headers=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status, payload):
        self._send(status, json.dumps(payload), "application/json; charset=utf-8")

    def _host_allowed(self):
        """Only answer requests addressed to this computer (blocks DNS tricks)."""
        name = (self.headers.get("Host") or "").rsplit(":", 1)[0].lower()
        return name in ("127.0.0.1", "localhost")

    def _site_param(self, query):
        site = (query.get("host") or [DEFAULT_SITE])[0].strip() or DEFAULT_SITE
        return site if valid_host(site) else None

    # -- routes -----------------------------------------------------------

    def do_GET(self):
        if not self._host_allowed():
            return self._json(403, {"error": "Forbidden"})

        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)

        if parsed.path == "/":
            page = PAGE.replace("__VERSION__", itcheck.__version__)
            return self._send(200, page, "text/html; charset=utf-8", {
                "Content-Security-Policy":
                    "default-src 'none'; style-src 'unsafe-inline'; "
                    "script-src 'unsafe-inline'; connect-src 'self'",
            })

        if parsed.path == "/api/system":
            return self._json(200, {"system": itcheck.sysinfo(), "disk": itcheck.disk_usage()})

        if parsed.path == "/api/net":
            site = self._site_param(query)
            if site is None:
                return self._json(400, {"error": "That is not a valid website name."})
            checks = itcheck.run_netcheck(site)
            return self._json(200, {
                "host": site,
                "checks": checks,
                "diagnosis": itcheck.diagnose(checks),
                "healthy": all(item["ok"] for item in checks),
            })

        if parsed.path == "/api/report":
            site = self._site_param(query)
            if site is None:
                return self._json(400, {"error": "That is not a valid website name."})
            text = itcheck.to_markdown(itcheck.build_report(site))
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
            return self._send(200, text, "text/markdown; charset=utf-8", {
                "Content-Disposition": 'attachment; filename="itcheck-report-%s.md"' % stamp,
            })

        return self._json(404, {"error": "Not found"})

    def do_POST(self):
        if not self._host_allowed():
            return self._json(403, {"error": "Forbidden"})
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/quit":
            if self.headers.get("X-ITCheck") != "1":
                return self._json(403, {"error": "Forbidden"})
            self._json(200, {"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return None
        return self._json(404, {"error": "Not found"})


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    # On Windows, address reuse would let a second copy steal the port.
    allow_reuse_address = os.name != "nt"


def make_server(port=DEFAULT_PORT):
    """Create the server, listening on this computer only."""
    return Server(("127.0.0.1", port), Handler)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="itcheck_app", description="IT Check local app.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help="port to listen on (default: %d)" % DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true",
                        help="do not open the page automatically")
    args = parser.parse_args(argv)

    try:
        server = make_server(args.port)
    except OSError as exc:
        print("Could not start on port %d: %s" % (args.port, exc))
        print("Try another port, for example: python itcheck_app.py --port 8800")
        return 1

    url = "http://127.0.0.1:%d/" % server.server_address[1]
    print("IT Check is running at %s" % url)
    print("Press Ctrl+C here, or click Quit in the page, to stop it.")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
