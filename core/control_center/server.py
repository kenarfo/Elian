# ==========================
# ELIAN — local Control Center GUI backend (stdlib HTTP + SSE)
# ==========================
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urlparse


class ControlCenterState:
    def __init__(self, orchestrator: Any = None) -> None:
        self.orchestrator = orchestrator
        self._events: List[dict] = []
        self._lock = threading.RLock()
        self._subs: List[threading.Event] = []
        if orchestrator is not None and hasattr(orchestrator, "register_gui_callback"):
            orchestrator.register_gui_callback(self.push_event)

    def push_event(self, event_type: str, data: dict) -> None:
        row = {
            "type": event_type,
            "data": dict(data or {}),
            "ts": time.time(),
        }
        with self._lock:
            self._events.append(row)
            self._events = self._events[-300:]
            for ev in self._subs:
                ev.set()

    def recent_events(self, after_ts: float = 0.0) -> List[dict]:
        with self._lock:
            return [e for e in self._events if float(e.get("ts") or 0) > after_ts]

    def wait_event(self, timeout: float = 15.0) -> None:
        flag = threading.Event()
        with self._lock:
            self._subs.append(flag)
        try:
            flag.wait(timeout=timeout)
        finally:
            with self._lock:
                if flag in self._subs:
                    self._subs.remove(flag)

    def snapshot(self) -> dict:
        if self.orchestrator is None:
            return {"error": "orchestrator_unavailable"}
        snap = self.orchestrator.snapshot()
        # Avatar / voice UI state from SelfModel — never invent activity.
        sm = snap.get("self_model") or {}
        rt = sm.get("runtime_state") or {}
        agents = snap.get("agents") or sm.get("active_agents") or []
        snap["avatar"] = {
            "state": sm.get("voice_state") or "IDLE",
            "health": sm.get("health") or "UNKNOWN",
            "working_agents": len(
                [a for a in agents if str(a.get("status")) == "WORKING"]
            ),
        }
        children = {}
        for a in agents:
            pid = str(a.get("parent_agent_id") or "")
            children.setdefault(pid, []).append(a)
        self_dev_agents = [
            a for a in agents if str(a.get("agent_type")) == "self_development"
        ]
        workers = []
        for sd in self_dev_agents:
            workers.extend(children.get(str(sd.get("agent_id") or ""), []))
        snap["live"] = {
            "tts": rt.get("tts") or {},
            "stt": rt.get("stt") or {},
            "hardware": rt.get("hardware") or (snap.get("resources") or {}).get("hardware") or {},
            "resource_pressure": rt.get("resource_pressure")
            or (snap.get("resources") or {}).get("pressure"),
            "self_development": {
                **(sm.get("development") or {}),
                "bg_thread_alive": bool(rt.get("bg_thread_alive")),
                "workers": [
                    {
                        "agent_id": w.get("agent_id"),
                        "role": w.get("role"),
                        "status": w.get("status"),
                        "task": w.get("task"),
                    }
                    for w in workers
                ],
            },
            "agent_graph": agents,
        }
        return snap


_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Elian Control Center</title>
<style>
:root {
  --bg0:#070b12; --bg1:#0d1524; --line:#1c2a40; --text:#d7e2f2;
  --muted:#7f93b0; --accent:#3aa0ff; --ok:#3dcf8e; --warn:#e0b44b; --err:#e85d5d;
  --glow:rgba(58,160,255,.35);
  font-family: "Segoe UI", "IBM Plex Sans", sans-serif;
}
*{box-sizing:border-box}
body{margin:0;background:radial-gradient(1200px 600px at 20% -10%,#13233a 0%,var(--bg0) 55%);
color:var(--text);min-height:100vh}
header{display:flex;align-items:center;gap:18px;padding:16px 22px;border-bottom:1px solid var(--line)}
.brand{font-size:28px;letter-spacing:.08em;font-weight:650}
.sub{color:var(--muted);font-size:12px}
.layout{display:grid;grid-template-columns:220px 1fr 280px;min-height:calc(100vh - 72px)}
nav{border-right:1px solid var(--line);padding:12px}
nav button{display:block;width:100%;text-align:left;background:transparent;border:0;
color:var(--text);padding:10px 12px;border-radius:8px;cursor:pointer;margin-bottom:4px}
nav button.active,nav button:hover{background:#132033}
main{padding:18px 20px;overflow:auto}
aside{border-left:1px solid var(--line);padding:16px}
.card{background:linear-gradient(180deg,var(--bg1),#0a1220);border:1px solid var(--line);
border-radius:12px;padding:14px;margin-bottom:12px}
.muted{color:var(--muted)}
.dot{width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:6px}
.dot.ok{background:var(--ok)}.dot.warn{background:var(--warn)}.dot.err{background:var(--err)}.dot.idle{background:var(--accent)}
#avatar{width:180px;height:180px;margin:12px auto;border-radius:50%;
background:radial-gradient(circle at 40% 35%, #6ec1ff, #12325a 55%, #070b12 72%);
box-shadow:0 0 40px var(--glow);transition:box-shadow .4s, filter .4s}
#avatar.listening{box-shadow:0 0 55px rgba(90,200,255,.55)}
#avatar.thinking{animation:pulse 1.4s ease-in-out infinite}
#avatar.speaking{box-shadow:0 0 60px rgba(80,255,200,.45)}
#avatar.working{box-shadow:0 0 50px rgba(160,120,255,.45)}
#avatar.warning{filter:saturate(1.2) hue-rotate(-20deg);box-shadow:0 0 40px rgba(224,180,75,.5)}
#avatar.error{filter:saturate(1.4) hue-rotate(-50deg);box-shadow:0 0 40px rgba(232,93,93,.55)}
#avatar.offline{filter:grayscale(.8);opacity:.55}
@keyframes pulse{0%,100%{transform:scale(1)}50%{transform:scale(1.03)}}
pre{white-space:pre-wrap;word-break:break-word;font-size:12px;color:#c5d4ea}
.tree{font-family:ui-monospace,Consolas,monospace;font-size:13px;line-height:1.55}
.row{display:flex;gap:8px;flex-wrap:wrap}
button.act{background:#153255;border:1px solid #2a4d78;color:var(--text);padding:8px 12px;border-radius:8px;cursor:pointer}
button.act:hover{background:#1b3d66}
input,textarea{width:100%;background:#0a1422;border:1px solid var(--line);color:var(--text);
border-radius:8px;padding:10px;margin-top:8px}
</style>
</head>
<body>
<header>
  <div>
    <div class="brand">ELIAN</div>
    <div class="sub">Local Control Center · multi-agent runtime</div>
  </div>
  <div class="muted" id="hdrStatus">connecting…</div>
</header>
<div class="layout">
  <nav id="tabs">
    <button data-tab="chat" class="active">Chat</button>
    <button data-tab="memory">Memory</button>
    <button data-tab="agents">Agents</button>
    <button data-tab="selfdev">Self-Development</button>
    <button data-tab="tools">Tools</button>
    <button data-tab="tasks">Tasks</button>
    <button data-tab="automations">Automations</button>
    <button data-tab="system">System</button>
    <button data-tab="map">System Map</button>
  </nav>
  <main id="main"></main>
  <aside>
    <div class="card">
      <div class="muted">Avatar</div>
      <div id="avatar" class="idle"></div>
      <div id="avatarLabel" class="muted" style="text-align:center">IDLE</div>
    </div>
    <div class="card">
      <div class="muted">Live events</div>
      <pre id="events" style="max-height:280px;overflow:auto"></pre>
    </div>
  </aside>
</div>
<script>
let SNAP = null;
let TAB = 'chat';
const tabs = document.querySelectorAll('#tabs button');
tabs.forEach(b => b.onclick = () => { TAB = b.dataset.tab; tabs.forEach(x=>x.classList.toggle('active',x===b)); render(); });

async function api(path, opts){
  const r = await fetch(path, opts);
  return r.json();
}

function avatarClass(state){
  const s = String(state||'IDLE').toUpperCase();
  if(s.includes('LISTEN')) return 'listening';
  if(s.includes('THINK')) return 'thinking';
  if(s.includes('SPEAK')) return 'speaking';
  if(s.includes('WORK')) return 'working';
  if(s.includes('WARN')) return 'warning';
  if(s.includes('ERROR')) return 'error';
  if(s.includes('OFF')) return 'offline';
  return 'idle';
}

function renderAgents(agents){
  const byParent = {};
  (agents||[]).forEach(a => {
    const p = a.parent_agent_id || 'ELIAN';
    (byParent[p]=byParent[p]||[]).push(a);
  });
  function walk(pid, depth){
    return (byParent[pid]||[]).map(a => {
      const dot = a.status==='WORKING'?'ok':(a.status==='FAILED'?'err':(a.status==='IDLE'?'idle':'warn'));
      let s = `${'  '.repeat(depth)}<span class="dot ${dot}"></span>${a.agent_type} · ${a.role} <span class="muted">[${a.status}]</span>`;
      if(a.task) s += `<div class="muted" style="margin-left:${depth*12+14}px">${a.task}</div>`;
      s += walk(a.agent_id, depth+1);
      return s;
    }).join('');
  }
  return `<div class="tree"><div><span class="dot ok"></span>ELIAN</div>${walk('ELIAN',1)}${walk('',1)}</div>`;
}

function render(){
  const sm = (SNAP&&SNAP.self_model)||{};
  const av = (SNAP&&SNAP.avatar)||{};
  document.getElementById('hdrStatus').textContent =
    `${sm.health||'?'} · ${sm.mode||'?'} · agents ${(sm.active_agents||[]).length}`;
  const ael = document.getElementById('avatar');
  ael.className = avatarClass(av.state || sm.voice_state);
  document.getElementById('avatarLabel').textContent = av.state || sm.voice_state || 'IDLE';

  const main = document.getElementById('main');
  if(TAB==='chat'){
    main.innerHTML = `<div class="card"><h3>Chat</h3>
      <div class="muted">One Elian personality · mic/speaker via voice runtime</div>
      <div>Focus: ${sm.current_focus||'—'}</div>
      <div>Voice: ${sm.voice_state||'IDLE'} · engine ${sm.voice_engine||'fallback'}</div>
      <textarea id="chatIn" rows="3" placeholder="Message Elian…"></textarea>
      <div class="row" style="margin-top:8px"><button class="act" id="sendBtn">Send</button></div>
      <pre id="chatOut"></pre></div>`;
    document.getElementById('sendBtn').onclick = async () => {
      const text = document.getElementById('chatIn').value;
      const res = await api('/api/chat', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text})});
      document.getElementById('chatOut').textContent = JSON.stringify(res,null,2);
      await refresh();
    };
  } else if(TAB==='memory'){
    const mem = (SNAP&&SNAP.memory)||{};
    main.innerHTML = `<div class="card"><h3>Memory</h3><pre>${JSON.stringify(mem,null,2)}</pre></div>`;
  } else if(TAB==='agents'){
    main.innerHTML = `<div class="card"><h3>Agents</h3>${renderAgents(SNAP&&SNAP.agents)}</div>
      <div class="card"><h3>Details</h3><pre>${JSON.stringify(SNAP&&SNAP.agents,null,2)}</pre></div>`;
  } else if(TAB==='selfdev'){
    const live = (SNAP&&SNAP.live)||{};
    const d = live.self_development || sm.development || {};
    const workers = d.workers||[];
    main.innerHTML = `<div class="card"><h3>Self-Development</h3>
      <div>Status: ${d.active?'WORKING':'IDLE'} ${d.bg_thread_alive?'(bg thread alive)':''}</div>
      <div>Cycle: ${d.cycle||0}</div><div>Phase: ${d.phase||'—'}</div>
      <div>Candidate: ${d.candidate||'—'}</div>
      <div>Last improvement: ${d.last_improvement||'—'}</div>
      <div>Last rollback: ${d.last_rollback||'—'}</div>
      <div class="muted" style="margin-top:8px">Agent workers</div>
      <pre>${workers.length?JSON.stringify(workers,null,2):'(none)'}</pre>
      <div class="row" style="margin-top:10px">
        <button class="act" data-c="start">Start</button>
        <button class="act" data-c="pause">Pause</button>
        <button class="act" data-c="resume">Resume</button>
        <button class="act" data-c="stop">Stop</button>
      </div></div>`;
    main.querySelectorAll('button.act').forEach(b => b.onclick = async () => {
      await api('/api/selfdev', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({command:b.dataset.c})});
      await refresh();
    });
  } else if(TAB==='tools'){
    main.innerHTML = `<div class="card"><h3>Tools</h3>
      <div>Installed: ${(sm.installed_tools||[]).join(', ')||'—'}</div>
      <div>Disabled: ${(sm.disabled_tools||[]).join(', ')||'—'}</div>
      <div>Capabilities: ${(sm.available_capabilities||[]).slice(0,40).join(', ')}</div></div>`;
  } else if(TAB==='tasks'){
    main.innerHTML = `<div class="card"><h3>Tasks</h3><pre>${JSON.stringify(sm.active_tasks||[],null,2)}</pre>
      <div class="muted">Queue: ${(SNAP&&SNAP.queue_len)||0}</div></div>`;
  } else if(TAB==='automations'){
    main.innerHTML = `<div class="card"><h3>Automations</h3>
      <div class="muted">Proactive / reminder watchers remain in ElianRuntime.</div>
      <div>Limitations: ${(sm.known_limitations||[]).join('; ')||'—'}</div></div>`;
  } else if(TAB==='system'){
    const live = (SNAP&&SNAP.live)||{};
    main.innerHTML = `<div class="card"><h3>System</h3>
      <div>Health: ${sm.health} · Mode: ${sm.mode} · Lang: ${sm.language}</div>
      <div>TTS: Fish=${(live.tts||{}).fish||'?'} · Fallback=${(live.tts||{}).fallback||'?'} · Active=${(live.tts||{}).active||'?'}</div>
      <div>STT: ${(live.stt||{}).engine||'?'} (${(live.stt||{}).language||'?'})</div>
      <div>GPU: ${(live.hardware||{}).gpu_name||'—'} · VRAM ${(live.hardware||{}).vram_reserved_gb||0}/${(live.hardware||{}).vram_total_gb||0} GB · pressure ${live.resource_pressure||'?'}</div>
      <pre style="margin-top:10px">${JSON.stringify({resources:SNAP&&SNAP.resources,hardware:live.hardware,tts:live.tts},null,2)}</pre>
      <div class="muted">Self description</div><pre>${(SNAP&&SNAP.describe)||''}</pre></div>`;
  } else if(TAB==='map'){
    main.innerHTML = `<div class="card"><h3>System Map</h3>${renderAgents(SNAP&&SNAP.agents)}
      <pre style="margin-top:12px">Elian
 ├── Conversation
 ├── Memory
 ├── Planning / Tools (dynamic)
 ├── Proactivity
 ├── Self-Development (+ workers)
 └── Voice</pre></div>`;
  }
}

async function refresh(){
  SNAP = await api('/api/snapshot');
  render();
}

async function eventLoop(){
  let cursor = 0;
  while(true){
    try{
      const data = await api('/api/events?after='+cursor);
      (data.events||[]).forEach(e => {
        cursor = Math.max(cursor, e.ts||0);
        const el = document.getElementById('events');
        el.textContent = `[${new Date((e.ts||0)*1000).toLocaleTimeString()}] ${e.type}\n` + el.textContent;
      });
      if((data.events||[]).length) await refresh();
    }catch(e){}
    await new Promise(r => setTimeout(r, 1200));
  }
}

refresh();
eventLoop();
</script>
</body>
</html>
"""


class ControlCenterHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, *, state: ControlCenterState):
        super().__init__(addr, handler)
        self.cc_state = state


def _make_handler(state: ControlCenterState):
    static_dir = Path(__file__).resolve().parents[2] / "gui" / "control-center" / "dist"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            print(f"[CONTROL] {self.command} {self.path}")

        def _json(self, obj: dict, code: int = 200) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _html(self, text: str) -> None:
            body = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = unquote(parsed.path or "/")
            if path in ("/", "/index.html"):
                # Prefer built React dist when present.
                index = static_dir / "index.html"
                if index.exists():
                    self._html(index.read_text(encoding="utf-8"))
                else:
                    self._html(_HTML)
                return
            if path == "/api/snapshot":
                snap = state.snapshot()
                orch = state.orchestrator
                if orch is not None:
                    snap["describe"] = orch.describe_self()
                self._json(snap)
                return
            if path.startswith("/api/events"):
                after = 0.0
                if "after=" in (parsed.query or ""):
                    try:
                        after = float(parsed.query.split("after=")[1].split("&")[0])
                    except Exception:
                        after = 0.0
                # Long-poll briefly for near-realtime without busy polling storms.
                if not state.recent_events(after):
                    state.wait_event(timeout=10.0)
                self._json({"events": state.recent_events(after)})
                return
            if path.startswith("/assets/") and static_dir.exists():
                target = (static_dir / path.lstrip("/")).resolve()
                if str(target).startswith(str(static_dir.resolve())) and target.exists():
                    data = target.read_bytes()
                    ctype = "application/javascript" if target.suffix == ".js" else "text/css"
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
            self._json({"error": "not_found"}, 404)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            path = unquote(parsed.path or "/")
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(min(length, 2 * 1024 * 1024)) if length else b"{}"
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                body = {}
            orch = state.orchestrator
            if path == "/api/chat":
                text = str(body.get("text") or "")
                if orch is None:
                    self._json({"error": "no_orchestrator"}, 503)
                    return
                # Safety: chat goes through orchestrator conversation path.
                result = orch.handle_user_text(text)
                state.push_event("gui.chat", {"summary": result.summary[:200]})
                self._json(result.to_dict())
                return
            if path == "/api/selfdev":
                cmd = str(body.get("command") or "")
                if orch is None:
                    self._json({"error": "no_orchestrator"}, 503)
                    return
                if cmd == "start":
                    orch.start_background_self_development(max_cycles=1)
                elif cmd == "pause":
                    orch.pause_background()
                elif cmd == "resume":
                    orch.resume_background()
                elif cmd == "stop":
                    orch.pause_background()
                    try:
                        from core.self_improve.self_development.state import (
                            DevelopmentStateStore,
                            default_state_path,
                        )

                        DevelopmentStateStore(default_state_path()).request_stop()
                    except Exception:
                        pass
                else:
                    self._json({"error": "unknown_command"}, 400)
                    return
                state.push_event("gui.selfdev", {"command": cmd})
                self._json({"ok": True, "command": cmd})
                return
            self._json({"error": "not_found"}, 404)

    return Handler


class ControlCenterHost:
    def __init__(self, server: ControlCenterHTTPServer, thread: threading.Thread, port: int):
        self.server = server
        self.thread = thread
        self.port = port

    def stop(self) -> None:
        try:
            self.server.shutdown()
        except Exception:
            pass


def maybe_start_control_center(
    *,
    orchestrator: Any = None,
    host: str = "127.0.0.1",
    port: int = 8787,
) -> Optional[ControlCenterHost]:
    try:
        from Config import get_config_value

        enabled = bool(get_config_value("gui", "enabled", default=True))
        host = str(get_config_value("gui", "host", default=host) or host)
        port = int(get_config_value("gui", "port", default=port) or port)
    except Exception:
        enabled = True
    if not enabled:
        return None

    state = ControlCenterState(orchestrator=orchestrator)
    httpd = ControlCenterHTTPServer((host, port), _make_handler(state), state=state)
    thread = threading.Thread(target=httpd.serve_forever, name="elian-control-center", daemon=True)
    thread.start()
    print(f"[CONTROL] Control Center at http://{host}:{port}/")
    return ControlCenterHost(httpd, thread, port)
