#!/usr/bin/env python3
"""MakeMKV AutoRip status dashboard web server."""

import asyncio
import json
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

SCRIPT_DIR = Path(__file__).parent
LOGS_DIR = SCRIPT_DIR / "logs"
SETTINGS_FILE = SCRIPT_DIR / "settings.cfg"

connected: set[WebSocket] = set()


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
    result = {
        "prgv_current": None, "prgv_total": None, "prgv_max": None,
        "prgc": None, "prgt": None, "title_current": None,
    }
    try:
        p = LOGS_DIR / progress_file_path
        if not p.exists():
            return result
        title_count = 0
        for line in p.read_text(errors="replace").splitlines():
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
                parts = line[5:].split(",", 2)
                if parts and parts[0] == "5057":
                    title_count += 1
                m = re.search(r'"([^"]*)"', line)
                if m:
                    result["prgc"] = m.group(1)
            elif line.startswith("PRGT:"):
                m = re.search(r'"([^"]*)"', line)
                if m:
                    result["prgt"] = m.group(1)
        if title_count > 0:
            result["title_current"] = title_count
    except Exception:
        pass
    return result


def get_title_total(log_file_path):
    try:
        p = LOGS_DIR / log_file_path
        if not p.exists():
            return None
        for line in p.read_text(errors="replace").splitlines():
            if line.startswith("MSG:5014,"):
                m = re.search(r',"(\d+)","(?:file|disc):', line)
                if m:
                    return int(m.group(1))
    except Exception:
        pass
    return None


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

            title_total = get_title_total(data.get("log_file", ""))

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
                "title_current": progress["title_current"],
                "title_total": title_total,
            })
        else:
            history.append(data)

    history.sort(key=lambda x: x.get("start_time", ""), reverse=True)
    history = history[:10]

    for item in history:
        try:
            st = datetime.fromisoformat(item["start_time"])
            et = datetime.fromisoformat(item["end_time"])
            item["duration_seconds"] = int((et - st).total_seconds())
        except Exception:
            item["duration_seconds"] = None

    return {"active": active, "history": history}


async def broadcaster():
    while True:
        await asyncio.sleep(1)
        if not connected:
            continue
        data = get_status_data()
        for ws in connected.copy():
            try:
                await ws.send_json(data)
            except Exception:
                connected.discard(ws)


@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(broadcaster())
    yield


app = FastAPI(lifespan=lifespan)


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
  .card-drive { font-size: 0.8rem; color: #8b949e; margin-bottom: 6px; }
  .title-counter { font-size: 0.82rem; color: #58a6ff; font-weight: 500; margin-bottom: 14px; }
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
  #footer { font-size: 0.75rem; color: #484f58; margin-top: 24px; display: flex; gap: 16px; }
  #conn-status { color: #f0883e; }
</style>
</head>
<body>
<h1><span class="dot"></span> MakeMKV AutoRip Dashboard</h1>
<h2>Active Rips</h2>
<div id="active-section"><div class="no-active">Connecting...</div></div>
<h2>Recent Completions</h2>
<div id="history-section"></div>
<div id="footer"><span id="last-updated"></span><span id="conn-status"></span></div>
<script>
function fmt_dur(s) {
  if (s == null) return '\u2014';
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (h > 0) return h + 'h ' + m + 'm';
  if (m > 0) return m + 'm ' + sec + 's';
  return sec + 's';
}
function fmt_pct(v) { return v != null ? v.toFixed(1) + '%' : '\u2014'; }
function fmt_time(iso) {
  if (!iso) return '\u2014';
  try { return new Date(iso).toLocaleString(); } catch(e) { return iso; }
}
function bar(pct, cls) {
  const w = pct != null ? Math.min(100, Math.max(0, pct)) : 0;
  return '<div class="progress-bar-bg"><div class="progress-bar-fill ' + cls + '" style="width:' + w + '%"></div></div>';
}
function esc(s) {
  return s ? String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;') : '';
}

function updateUI(data) {
  const ac = document.getElementById('active-section');
  if (!data.active || data.active.length === 0) {
    ac.innerHTML = '<div class="no-active">No active rips.</div>';
  } else {
    ac.innerHTML = '<div class="cards">' + data.active.map(r => {
      const tp = fmt_pct(r.title_pct), op = fmt_pct(r.overall_pct);
      const titleLine = r.title_current != null
        ? '<div class="title-counter">Title ' + r.title_current + (r.title_total != null ? ' of ' + r.title_total : '') + '</div>'
        : '';
      return '<div class="card">'
        + '<div class="card-title">' + esc(r.title || 'Unknown') + '</div>'
        + '<div class="card-drive">' + esc(r.drive || '') + '</div>'
        + titleLine
        + '<div class="progress-label"><span>Title progress</span><span>' + tp + '</span></div>'
        + bar(r.title_pct, '')
        + '<div class="progress-label"><span>Overall progress</span><span>' + op + '</span></div>'
        + bar(r.overall_pct, 'overall')
        + (r.current_op ? '<div class="op-label">Current operation</div><div class="op-value">' + esc(r.current_op) + '</div>' : '')
        + (r.overall_op ? '<div class="op-label">Overall</div><div class="op-value">' + esc(r.overall_op) + '</div>' : '')
        + '<div class="elapsed">Elapsed: ' + fmt_dur(r.elapsed_seconds) + '</div>'
        + '</div>';
    }).join('') + '</div>';
  }

  const hs = document.getElementById('history-section');
  if (!data.history || data.history.length === 0) {
    hs.innerHTML = '<div class="no-active">No completed rips yet.</div>';
  } else {
    hs.innerHTML = '<table><thead><tr><th>Title</th><th>Drive</th><th>Started</th><th>Duration</th><th>Status</th></tr></thead><tbody>'
      + data.history.map(r => {
          const badge = r.status === 'complete'
            ? '<span class="badge complete">Complete</span>'
            : '<span class="badge failed">Failed</span>';
          return '<tr><td>' + esc(r.title || '\u2014') + '</td><td>' + esc(r.drive || '\u2014') + '</td>'
            + '<td>' + fmt_time(r.start_time) + '</td>'
            + '<td>' + fmt_dur(r.duration_seconds) + '</td>'
            + '<td>' + badge + '</td></tr>';
        }).join('')
      + '</tbody></table>';
  }

  document.getElementById('last-updated').textContent = 'Updated: ' + new Date().toLocaleTimeString();
}

let ws, reconnTimer;
function connect() {
  clearTimeout(reconnTimer);
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(proto + '//' + location.host + '/ws');
  ws.onopen = () => document.getElementById('conn-status').textContent = '';
  ws.onmessage = evt => { try { updateUI(JSON.parse(evt.data)); } catch(e) {} };
  ws.onclose = () => {
    document.getElementById('conn-status').textContent = 'Disconnected \u2014 reconnecting...';
    reconnTimer = setTimeout(connect, 3000);
  };
  ws.onerror = () => ws.close();
}
connect();
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return DASHBOARD_HTML


@app.get("/api/status")
async def api_status():
    return JSONResponse(get_status_data())


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected.add(websocket)
    try:
        await websocket.send_json(get_status_data())
        async for _ in websocket.iter_text():
            pass  # ignore client messages, just keep connection alive
    except WebSocketDisconnect:
        pass
    finally:
        connected.discard(websocket)


if __name__ == "__main__":
    import uvicorn
    port = read_webport()
    print(f"[INFO] Dashboard running at http://0.0.0.0:{port}", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
