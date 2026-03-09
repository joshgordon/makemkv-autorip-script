#!/usr/bin/env python3
"""MakeMKV AutoRip status dashboard web server."""

import json
import os
import re
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
LOGS_DIR = SCRIPT_DIR / "logs"
SETTINGS_FILE = SCRIPT_DIR / "settings.cfg"


def read_webport():
    try:
        for line in SETTINGS_FILE.read_text().splitlines():
            if line.startswith("webport"):
                val = line.split("=", 1)[1].split("#")[0].strip()
                if val.isdigit():
                    return int(val)
    except Exception:
        pass
    return 8080


def parse_progress(progress_file_path):
    """Read last 8KB of progress file and extract latest PRGV/PRGC/PRGT values."""
    result = {"prgv_current": None, "prgv_total": None, "prgv_max": None,
              "prgc": None, "prgt": None}
    try:
        p = LOGS_DIR / progress_file_path
        if not p.exists():
            return result
        size = p.stat().st_size
        read_size = min(8192, size)
        with open(p, "rb") as f:
            if size > read_size:
                f.seek(-read_size, 2)
            chunk = f.read().decode("utf-8", errors="replace")

        for line in chunk.splitlines():
            if line.startswith("PRGV:"):
                parts = line[5:].split(",")
                if len(parts) >= 3:
                    try:
                        result["prgv_current"] = int(parts[0])
                        result["prgv_total"] = int(parts[1])
                        result["prgv_max"] = int(parts[2])
                    except ValueError:
                        pass
            elif line.startswith("PRGC:"):
                m = re.search(r'"([^"]*)"', line)
                if m:
                    result["prgc"] = m.group(1)
            elif line.startswith("PRGT:"):
                m = re.search(r'"([^"]*)"', line)
                if m:
                    result["prgt"] = m.group(1)
    except Exception:
        pass
    return result


def get_status_data():
    active = []
    history = []

    if not LOGS_DIR.exists():
        return {"active": active, "history": history}

    for f in LOGS_DIR.glob("status_*.json"):
        try:
            data = json.loads(f.read_text())
        except Exception:
            continue

        if data.get("status") == "ripping":
            progress = parse_progress(data.get("progress_file", ""))
            prgv_max = progress["prgv_max"] or 65536
            title_pct = None
            overall_pct = None
            if progress["prgv_current"] is not None:
                title_pct = round(progress["prgv_current"] / prgv_max * 100, 1)
            if progress["prgv_total"] is not None:
                overall_pct = round(progress["prgv_total"] / prgv_max * 100, 1)

            start_time = data.get("start_time")
            elapsed = None
            if start_time:
                try:
                    st = datetime.fromisoformat(start_time)
                    elapsed = int((datetime.now(timezone.utc) - st).total_seconds())
                except Exception:
                    pass

            active.append({
                "drive": data.get("drive"),
                "title": data.get("title"),
                "start_time": start_time,
                "elapsed_seconds": elapsed,
                "current_op": progress["prgc"],
                "overall_op": progress["prgt"],
                "title_pct": title_pct,
                "overall_pct": overall_pct,
            })
        else:
            history.append(data)

    history.sort(key=lambda x: x.get("start_time", ""), reverse=True)
    history = history[:10]

    # Compute durations for history
    for item in history:
        try:
            st = datetime.fromisoformat(item["start_time"])
            et = datetime.fromisoformat(item["end_time"])
            item["duration_seconds"] = int((et - st).total_seconds())
        except Exception:
            item["duration_seconds"] = None

    return {"active": active, "history": history}


DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MakeMKV AutoRip Dashboard</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0d1117; color: #c9d1d9; font-family: 'Segoe UI', system-ui, sans-serif; padding: 24px; min-height: 100vh; }
  h1 { font-size: 1.5rem; font-weight: 600; color: #e6edf3; margin-bottom: 24px; display: flex; align-items: center; gap: 10px; }
  h1 span.dot { width: 10px; height: 10px; border-radius: 50%; background: #3fb950; display: inline-block; animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
  h2 { font-size: 1rem; color: #8b949e; text-transform: uppercase; letter-spacing: .08em; margin-bottom: 14px; margin-top: 28px; }
  .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 16px; }
  .card { background: #161b22; border: 1px solid #30363d; border-radius: 10px; padding: 20px; }
  .card-title { font-size: 1.05rem; font-weight: 600; color: #e6edf3; margin-bottom: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .card-drive { font-size: 0.8rem; color: #8b949e; margin-bottom: 14px; }
  .progress-label { font-size: 0.78rem; color: #8b949e; margin-bottom: 4px; display: flex; justify-content: space-between; }
  .progress-bar-bg { background: #21262d; border-radius: 4px; height: 8px; margin-bottom: 12px; overflow: hidden; }
  .progress-bar-fill { height: 100%; border-radius: 4px; background: #238636; transition: width 0.5s ease; }
  .progress-bar-fill.overall { background: #1f6feb; }
  .op-label { font-size: 0.82rem; color: #8b949e; margin-top: 4px; }
  .op-value { font-size: 0.88rem; color: #c9d1d9; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .elapsed { font-size: 0.78rem; color: #8b949e; margin-top: 10px; }
  .no-active { color: #8b949e; font-style: italic; padding: 16px 0; }
  table { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
  th { text-align: left; padding: 8px 12px; color: #8b949e; border-bottom: 1px solid #21262d; font-weight: 500; }
  td { padding: 10px 12px; border-bottom: 1px solid #21262d; }
  tr:last-child td { border-bottom: none; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 0.75rem; font-weight: 600; }
  .badge.complete { background: #1a4731; color: #3fb950; }
  .badge.failed { background: #3d1a1a; color: #f85149; }
  #last-updated { font-size: 0.75rem; color: #484f58; margin-top: 24px; }
</style>
</head>
<body>
<h1><span class="dot"></span> MakeMKV AutoRip Dashboard</h1>
<h2>Active Rips</h2>
<div id="active-section"><div class="no-active">Loading...</div></div>
<h2>Recent Completions</h2>
<div id="history-section"></div>
<div id="last-updated"></div>
<script>
function fmt_dur(s) {
  if (s == null) return '—';
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (h > 0) return h + 'h ' + m + 'm';
  if (m > 0) return m + 'm ' + sec + 's';
  return sec + 's';
}
function fmt_pct(v) { return v != null ? v.toFixed(1) + '%' : '—'; }
function fmt_time(iso) {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleString(); } catch(e) { return iso; }
}
function bar(pct, cls) {
  const w = pct != null ? Math.min(100, Math.max(0, pct)) : 0;
  return '<div class="progress-bar-bg"><div class="progress-bar-fill ' + cls + '" style="width:' + w + '%"></div></div>';
}

async function refresh() {
  try {
    const resp = await fetch('/api/status');
    const data = await resp.json();

    // Active
    const ac = document.getElementById('active-section');
    if (data.active.length === 0) {
      ac.innerHTML = '<div class="no-active">No active rips.</div>';
    } else {
      ac.innerHTML = '<div class="cards">' + data.active.map(r => {
        const tp = fmt_pct(r.title_pct), op = fmt_pct(r.overall_pct);
        return '<div class="card">'
          + '<div class="card-title">' + (r.title || 'Unknown') + '</div>'
          + '<div class="card-drive">' + (r.drive || '') + '</div>'
          + '<div class="progress-label"><span>Title progress</span><span>' + tp + '</span></div>'
          + bar(r.title_pct, '')
          + '<div class="progress-label"><span>Overall progress</span><span>' + op + '</span></div>'
          + bar(r.overall_pct, 'overall')
          + (r.current_op ? '<div class="op-label">Current operation</div><div class="op-value">' + r.current_op + '</div>' : '')
          + (r.overall_op ? '<div class="op-label">Overall</div><div class="op-value">' + r.overall_op + '</div>' : '')
          + '<div class="elapsed">Elapsed: ' + fmt_dur(r.elapsed_seconds) + '</div>'
          + '</div>';
      }).join('') + '</div>';
    }

    // History
    const hs = document.getElementById('history-section');
    if (!data.history || data.history.length === 0) {
      hs.innerHTML = '<div class="no-active">No completed rips yet.</div>';
    } else {
      hs.innerHTML = '<table><thead><tr><th>Title</th><th>Drive</th><th>Started</th><th>Duration</th><th>Status</th></tr></thead><tbody>'
        + data.history.map(r => {
            const badge = r.status === 'complete'
              ? '<span class="badge complete">Complete</span>'
              : '<span class="badge failed">Failed</span>';
            return '<tr><td>' + (r.title || '—') + '</td><td>' + (r.drive || '—') + '</td>'
              + '<td>' + fmt_time(r.start_time) + '</td>'
              + '<td>' + fmt_dur(r.duration_seconds) + '</td>'
              + '<td>' + badge + '</td></tr>';
          }).join('')
        + '</tbody></table>';
    }

    document.getElementById('last-updated').textContent = 'Last updated: ' + new Date().toLocaleTimeString();
  } catch(e) {
    console.error('Failed to fetch status:', e);
  }
}

refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # suppress access log spam

    def do_GET(self):
        if self.path == "/api/status":
            data = get_status_data()
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path in ("/", "/index.html"):
            body = DASHBOARD_HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


if __name__ == "__main__":
    port = read_webport()
    server = HTTPServer(("", port), Handler)
    print(f"[INFO] Dashboard running at http://0.0.0.0:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
