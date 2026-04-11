#!/usr/bin/env python3
"""MakeMKV AutoRip status dashboard web server."""

import asyncio
import json
import re
import shutil
import subprocess
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

SCRIPT_DIR = Path(__file__).parent
LOGS_DIR = SCRIPT_DIR / "logs"
SETTINGS_FILE = SCRIPT_DIR / "settings.cfg"

connected: set[WebSocket] = set()


def read_setting(key):
    try:
        for line in SETTINGS_FILE.read_text().splitlines():
            if line.startswith(key):
                val = line.split("=", 1)[1].split("#")[0].strip().strip('"')
                return val
    except Exception:
        pass
    return None


def read_webport():
    val = read_setting("webport")
    if val and val.isdigit():
        return int(val)
    return 8080


def get_disk_usage():
    path = read_setting("outputdir") or "/"
    try:
        usage = shutil.disk_usage(path)
        return {
            "path": path,
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "pct_used": round(usage.used / usage.total * 100, 1),
        }
    except Exception:
        return None


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
        return {"active": active, "history": history, "disk": get_disk_usage()}

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

    return {"active": active, "history": history, "disk": get_disk_usage()}


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
  .disk-bar { margin-bottom: 24px; }
  .disk-info { display: flex; justify-content: space-between; font-size: 0.82rem; color: #8b949e; margin-bottom: 6px; }
  .disk-info .disk-path { color: #c9d1d9; }
  .disk-bar-bg { background: #21262d; border-radius: 4px; height: 10px; overflow: hidden; }
  .disk-bar-fill { height: 100%; border-radius: 4px; background: #238636; transition: width 0.5s ease; }
  .disk-bar-fill.warn { background: #d29922; }
  .disk-bar-fill.danger { background: #f85149; }
  .card-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 4px; }
  .card-header .card-title { margin-bottom: 0; flex: 1; min-width: 0; }
  .btn-eject { background: #21262d; border: 1px solid #30363d; color: #8b949e; font-size: 0.75rem; font-weight: 600; padding: 4px 10px; border-radius: 6px; cursor: pointer; white-space: nowrap; margin-left: 10px; flex-shrink: 0; transition: background 0.15s, color 0.15s, border-color 0.15s; }
  .btn-eject:hover { background: #3d1a1a; border-color: #f85149; color: #f85149; }
  .modal-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.6); z-index: 100; align-items: center; justify-content: center; }
  .modal-overlay.active { display: flex; }
  .modal { background: #161b22; border: 1px solid #30363d; border-radius: 12px; padding: 28px 28px 24px; max-width: 380px; width: 90%; }
  .modal h3 { font-size: 1rem; font-weight: 600; color: #e6edf3; margin-bottom: 8px; }
  .modal p { font-size: 0.88rem; color: #8b949e; margin-bottom: 20px; line-height: 1.5; }
  .modal-drive { color: #c9d1d9; font-family: monospace; }
  .modal-actions { display: flex; gap: 10px; justify-content: flex-end; }
  .btn-cancel { background: #21262d; border: 1px solid #30363d; color: #c9d1d9; font-size: 0.85rem; font-weight: 600; padding: 7px 16px; border-radius: 6px; cursor: pointer; }
  .btn-cancel:hover { background: #30363d; }
  .btn-confirm-eject { background: #3d1a1a; border: 1px solid #f85149; color: #f85149; font-size: 0.85rem; font-weight: 600; padding: 7px 16px; border-radius: 6px; cursor: pointer; }
  .btn-confirm-eject:hover { background: #f85149; color: #fff; }
</style>
</head>
<body>
<h1><span class="dot"></span> MakeMKV AutoRip Dashboard</h1>
<div id="disk-section"></div>
<h2>Active Rips</h2>
<div id="active-section"><div class="no-active">Connecting...</div></div>
<h2>Recent Completions</h2>
<div id="history-section"></div>
<div id="footer"><span id="last-updated"></span><span id="conn-status"></span></div>

<div class="modal-overlay" id="eject-modal">
  <div class="modal">
    <h3>Eject Drive?</h3>
    <p>Are you sure you want to eject <span class="modal-drive" id="modal-drive-name"></span>?</p>
    <div class="modal-actions">
      <button class="btn-cancel" id="modal-cancel">Cancel</button>
      <button class="btn-confirm-eject" id="modal-confirm">Eject</button>
    </div>
  </div>
</div>
<script>
function fmt_bytes(b) {
  if (b == null) return '\u2014';
  const units = ['B','KB','MB','GB','TB'];
  let i = 0;
  while (b >= 1024 && i < units.length - 1) { b /= 1024; i++; }
  return b.toFixed(1) + '\u00a0' + units[i];
}
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
function esc(s) {
  return s ? String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;') : '';
}

function driveKey(drive) {
  return (drive || '').replace(/[^a-zA-Z0-9]/g, '_');
}
function buildCard(r) {
  const key = driveKey(r.drive);
  const tw = r.title_pct != null ? Math.min(100, Math.max(0, r.title_pct)) : 0;
  const ow = r.overall_pct != null ? Math.min(100, Math.max(0, r.overall_pct)) : 0;
  const div = document.createElement('div');
  div.className = 'card';
  div.dataset.drive = key;
  div.innerHTML =
    '<div class="card-header">'
    + '<div class="card-title" data-f="title">' + esc(r.title || 'Unknown') + '</div>'
    + (r.drive ? '<button class="btn-eject" onclick="confirmEject(\\'' + esc(r.drive) + '\\')">Eject</button>' : '')
    + '</div>'
    + '<div class="card-drive">' + esc(r.drive || '') + '</div>'
    + '<div class="title-counter" data-f="tc"' + (r.title_current == null ? ' style="display:none"' : '') + '>'
    + (r.title_current != null ? 'Title ' + r.title_current + (r.title_total != null ? ' of ' + r.title_total : '') : '')
    + '</div>'
    + '<div class="progress-label"><span>Title progress</span><span data-f="tp">' + fmt_pct(r.title_pct) + '</span></div>'
    + '<div class="progress-bar-bg"><div class="progress-bar-fill" data-f="tb" style="width:' + tw + '%"></div></div>'
    + '<div class="progress-label"><span>Overall progress</span><span data-f="op">' + fmt_pct(r.overall_pct) + '</span></div>'
    + '<div class="progress-bar-bg"><div class="progress-bar-fill overall" data-f="ob" style="width:' + ow + '%"></div></div>'
    + '<div class="op-label" data-f="cop-label"' + (r.current_op ? '' : ' style="display:none"') + '>Current operation</div>'
    + '<div class="op-value" data-f="cop"' + (r.current_op ? '' : ' style="display:none"') + '>' + esc(r.current_op || '') + '</div>'
    + '<div class="op-label" data-f="oop-label"' + (r.overall_op ? '' : ' style="display:none"') + '>Overall</div>'
    + '<div class="op-value" data-f="oop"' + (r.overall_op ? '' : ' style="display:none"') + '>' + esc(r.overall_op || '') + '</div>'
    + '<div class="elapsed" data-f="el">Elapsed: ' + fmt_dur(r.elapsed_seconds) + '</div>';
  return div;
}
function updateCard(card, r) {
  const f = name => card.querySelector('[data-f="' + name + '"]');
  const tw = r.title_pct != null ? Math.min(100, Math.max(0, r.title_pct)) : 0;
  const ow = r.overall_pct != null ? Math.min(100, Math.max(0, r.overall_pct)) : 0;
  f('title').textContent = r.title || 'Unknown';
  const tc = f('tc');
  if (r.title_current != null) { tc.textContent = 'Title ' + r.title_current + (r.title_total != null ? ' of ' + r.title_total : ''); tc.style.display = ''; }
  else { tc.style.display = 'none'; }
  f('tp').textContent = fmt_pct(r.title_pct);
  f('tb').style.width = tw + '%';
  f('op').textContent = fmt_pct(r.overall_pct);
  f('ob').style.width = ow + '%';
  const copLabel = f('cop-label'), cop = f('cop');
  if (r.current_op) { cop.textContent = r.current_op; cop.style.display = ''; copLabel.style.display = ''; }
  else { cop.style.display = 'none'; copLabel.style.display = 'none'; }
  const oopLabel = f('oop-label'), oop = f('oop');
  if (r.overall_op) { oop.textContent = r.overall_op; oop.style.display = ''; oopLabel.style.display = ''; }
  else { oop.style.display = 'none'; oopLabel.style.display = 'none'; }
  f('el').textContent = 'Elapsed: ' + fmt_dur(r.elapsed_seconds);
}
function renderActive(active, container) {
  if (!active || active.length === 0) { container.innerHTML = '<div class="no-active">No active rips.</div>'; return; }
  let wrap = container.querySelector('.cards');
  if (!wrap) { container.innerHTML = ''; wrap = document.createElement('div'); wrap.className = 'cards'; container.appendChild(wrap); }
  const newKeys = new Set(active.map(r => driveKey(r.drive)));
  for (const card of [...wrap.children]) { if (!newKeys.has(card.dataset.drive)) wrap.removeChild(card); }
  for (const r of active) {
    const existing = wrap.querySelector('[data-drive="' + driveKey(r.drive) + '"]');
    if (existing) { updateCard(existing, r); } else { wrap.appendChild(buildCard(r)); }
  }
}

function updateUI(data) {
  const ac = document.getElementById('active-section');
  renderActive(data.active, ac);

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

  const ds = document.getElementById('disk-section');
  if (data.disk) {
    const d = data.disk;
    const pct = d.pct_used;
    const cls = pct >= 90 ? 'danger' : pct >= 75 ? 'warn' : '';
    ds.innerHTML = '<div class="disk-bar">'
      + '<div class="disk-info"><span class="disk-path">' + esc(d.path) + '</span>'
      + '<span>' + fmt_bytes(d.used) + ' used of ' + fmt_bytes(d.total) + ' &mdash; ' + fmt_bytes(d.free) + ' free (' + pct + '% used)</span></div>'
      + '<div class="disk-bar-bg"><div class="disk-bar-fill ' + cls + '" style="width:' + Math.min(100, pct) + '%"></div></div>'
      + '</div>';
  } else {
    ds.innerHTML = '';
  }

  document.getElementById('last-updated').textContent = 'Updated: ' + new Date().toLocaleTimeString();
}

let pendingEjectDrive = null;
const ejectModal = document.getElementById('eject-modal');
const modalDriveName = document.getElementById('modal-drive-name');

function confirmEject(drive) {
  pendingEjectDrive = drive;
  modalDriveName.textContent = drive;
  ejectModal.classList.add('active');
}

document.getElementById('modal-cancel').addEventListener('click', () => {
  ejectModal.classList.remove('active');
  pendingEjectDrive = null;
});

ejectModal.addEventListener('click', (e) => {
  if (e.target === ejectModal) {
    ejectModal.classList.remove('active');
    pendingEjectDrive = null;
  }
});

document.getElementById('modal-confirm').addEventListener('click', async () => {
  if (!pendingEjectDrive) return;
  const drive = pendingEjectDrive;
  ejectModal.classList.remove('active');
  pendingEjectDrive = null;
  try {
    const resp = await fetch('/api/eject', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({drive})
    });
    const data = await resp.json();
    if (!data.ok) console.error('Eject failed:', data.error);
  } catch(e) {
    console.error('Eject request failed:', e);
  }
});

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


@app.post("/api/eject")
async def api_eject(request: Request):
    body = await request.json()
    drive = body.get("drive", "")
    # Only allow /dev/sr*, /dev/dvd*, /dev/cdrom* to prevent injection
    if not re.fullmatch(r"/dev/(sr\d+|dvd\w*|cdrom\w*)", drive):
        return JSONResponse({"ok": False, "error": "Invalid drive path"}, status_code=400)
    try:
        subprocess.run(["eject", drive], check=True, timeout=10)
        return JSONResponse({"ok": True})
    except subprocess.CalledProcessError as e:
        return JSONResponse({"ok": False, "error": f"eject failed: {e}"}, status_code=500)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


if __name__ == "__main__":
    import uvicorn
    port = read_webport()
    print(f"[INFO] Dashboard running at http://0.0.0.0:{port}", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
