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
  [x-cloak] { display: none !important; }
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
<script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3/dist/cdn.min.js"></script>
</head>
<body x-data="dashboard()" x-cloak>
<h1><span class="dot"></span> MakeMKV AutoRip Dashboard</h1>

<div x-show="disk" class="disk-bar">
  <div class="disk-info">
    <span class="disk-path" x-text="disk ? disk.path : ''"></span>
    <span x-text="diskSummary()"></span>
  </div>
  <div class="disk-bar-bg">
    <div class="disk-bar-fill" :class="diskClass()" :style="'width:' + (disk ? Math.min(100, disk.pct_used) : 0) + '%'"></div>
  </div>
</div>

<h2>Active Rips</h2>
<div x-show="active.length === 0" class="no-active">No active rips.</div>
<div class="cards" x-show="active.length > 0">
  <template x-for="r in active" :key="r.drive">
    <div class="card">
      <div class="card-header">
        <div class="card-title" x-text="r.title || 'Unknown'"></div>
        <button class="btn-eject" x-show="r.drive" @click="pendingEject = r.drive">Eject</button>
      </div>
      <div class="card-drive" x-text="r.drive || ''"></div>
      <div class="title-counter" x-show="r.title_current != null" x-text="titleCounter(r)"></div>
      <div class="progress-label"><span>Title progress</span><span x-text="fmtPct(r.title_pct)"></span></div>
      <div class="progress-bar-bg"><div class="progress-bar-fill" :style="'width:' + clamp(r.title_pct) + '%'"></div></div>
      <div class="progress-label"><span>Overall progress</span><span x-text="fmtPct(r.overall_pct)"></span></div>
      <div class="progress-bar-bg"><div class="progress-bar-fill overall" :style="'width:' + clamp(r.overall_pct) + '%'"></div></div>
      <template x-if="r.current_op">
        <div><div class="op-label">Current operation</div><div class="op-value" x-text="r.current_op"></div></div>
      </template>
      <template x-if="r.overall_op">
        <div><div class="op-label">Overall</div><div class="op-value" x-text="r.overall_op"></div></div>
      </template>
      <div class="elapsed" x-text="'Elapsed: ' + fmtDur(r.elapsed_seconds)"></div>
    </div>
  </template>
</div>

<h2>Recent Completions</h2>
<div x-show="history.length === 0" class="no-active">No completed rips yet.</div>
<table x-show="history.length > 0">
  <thead><tr><th>Title</th><th>Drive</th><th>Started</th><th>Duration</th><th>Status</th></tr></thead>
  <tbody>
    <template x-for="r in history" :key="r.start_time">
      <tr>
        <td x-text="r.title || '\u2014'"></td>
        <td x-text="r.drive || '\u2014'"></td>
        <td x-text="fmtTime(r.start_time)"></td>
        <td x-text="fmtDur(r.duration_seconds)"></td>
        <td>
          <span class="badge complete" x-show="r.status === 'complete'">Complete</span>
          <span class="badge failed" x-show="r.status !== 'complete'">Failed</span>
        </td>
      </tr>
    </template>
  </tbody>
</table>

<div id="footer">
  <span x-text="lastUpdated"></span>
  <span id="conn-status" x-text="connStatus"></span>
</div>

<div class="modal-overlay" :class="{ active: pendingEject }" @click.self="pendingEject = null">
  <div class="modal">
    <h3>Eject Drive?</h3>
    <p>Are you sure you want to eject <span class="modal-drive" x-text="pendingEject"></span>?</p>
    <div class="modal-actions">
      <button class="btn-cancel" @click="pendingEject = null">Cancel</button>
      <button class="btn-confirm-eject" @click="doEject()">Eject</button>
    </div>
  </div>
</div>

<script>
function dashboard() {
  return {
    active: [],
    history: [],
    disk: null,
    pendingEject: null,
    lastUpdated: '',
    connStatus: 'Connecting...',

    init() { this.connect(); },

    connect() {
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
      const ws = new WebSocket(proto + '//' + location.host + '/ws');
      ws.onopen = () => { this.connStatus = ''; };
      ws.onmessage = evt => {
        try {
          const d = JSON.parse(evt.data);
          this.active = d.active || [];
          this.history = d.history || [];
          this.disk = d.disk || null;
          this.lastUpdated = 'Updated: ' + new Date().toLocaleTimeString();
        } catch(e) {}
      };
      ws.onclose = () => {
        this.connStatus = 'Disconnected \u2014 reconnecting...';
        setTimeout(() => this.connect(), 3000);
      };
      ws.onerror = () => ws.close();
    },

    async doEject() {
      const drive = this.pendingEject;
      this.pendingEject = null;
      try {
        const resp = await fetch('/api/eject', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ drive })
        });
        const data = await resp.json();
        if (!data.ok) console.error('Eject failed:', data.error);
      } catch(e) { console.error('Eject request failed:', e); }
    },

    fmtBytes(b) {
      if (b == null) return '\u2014';
      const units = ['B', 'KB', 'MB', 'GB', 'TB'];
      let i = 0;
      while (b >= 1024 && i < units.length - 1) { b /= 1024; i++; }
      return b.toFixed(1) + '\u00a0' + units[i];
    },
    fmtDur(s) {
      if (s == null) return '\u2014';
      const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
      if (h > 0) return h + 'h ' + m + 'm';
      if (m > 0) return m + 'm ' + sec + 's';
      return sec + 's';
    },
    fmtPct(v) { return v != null ? v.toFixed(1) + '%' : '\u2014'; },
    fmtTime(iso) {
      if (!iso) return '\u2014';
      try { return new Date(iso).toLocaleString(); } catch(e) { return iso; }
    },
    clamp(v) { return v != null ? Math.min(100, Math.max(0, v)) : 0; },
    titleCounter(r) {
      return 'Title ' + r.title_current + (r.title_total != null ? ' of ' + r.title_total : '');
    },
    diskClass() {
      if (!this.disk) return '';
      return this.disk.pct_used >= 90 ? 'danger' : this.disk.pct_used >= 75 ? 'warn' : '';
    },
    diskSummary() {
      if (!this.disk) return '';
      const d = this.disk;
      return this.fmtBytes(d.used) + ' used of ' + this.fmtBytes(d.total) + ' \u2014 ' + this.fmtBytes(d.free) + ' free (' + d.pct_used + '% used)';
    },
  };
}
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
