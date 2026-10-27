import { useCallback, useEffect, useMemo, useState } from "react";

type Agent = {
  agent_id: string;
  agent_type: string;
  role: string;
  status: string;
  task?: string;
  parent_agent_id?: string;
  persistent?: boolean;
};

type Snapshot = {
  self_model?: Record<string, any>;
  agents?: Agent[];
  resources?: Record<string, any>;
  memory?: Record<string, any>;
  avatar?: { state?: string; health?: string; working_agents?: number };
  describe?: string;
  queue_len?: number;
};

const TABS = [
  "Chat",
  "Memory",
  "Agents",
  "Self-Development",
  "Tools",
  "Tasks",
  "Automations",
  "System",
  "Map",
] as const;

function avatarClass(state?: string): string {
  const s = String(state || "IDLE").toUpperCase();
  if (s.includes("LISTEN")) return "listening";
  if (s.includes("THINK")) return "thinking";
  if (s.includes("SPEAK")) return "speaking";
  if (s.includes("WORK")) return "working";
  if (s.includes("WARN")) return "warning";
  if (s.includes("ERROR")) return "error";
  if (s.includes("OFF")) return "offline";
  return "idle";
}

async function api(path: string, init?: RequestInit) {
  const r = await fetch(path, init);
  return r.json();
}

export default function App() {
  const [tab, setTab] = useState<(typeof TABS)[number]>("Chat");
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [events, setEvents] = useState<string[]>([]);
  const [chat, setChat] = useState("");
  const [chatOut, setChatOut] = useState("");

  const refresh = useCallback(async () => {
    const data = await api("/api/snapshot");
    setSnap(data);
  }, []);

  useEffect(() => {
    refresh();
    let cursor = 0;
    let alive = true;
    (async () => {
      while (alive) {
        try {
          const data = await api(`/api/events?after=${cursor}`);
          const rows = data.events || [];
          if (rows.length) {
            cursor = Math.max(cursor, ...rows.map((e: any) => e.ts || 0));
            setEvents((prev) =>
              [
                ...rows.map(
                  (e: any) =>
                    `[${new Date((e.ts || 0) * 1000).toLocaleTimeString()}] ${e.type}`
                ),
                ...prev,
              ].slice(0, 80)
            );
            await refresh();
          }
        } catch {
          /* backend may be restarting */
        }
        await new Promise((r) => setTimeout(r, 1200));
      }
    })();
    return () => {
      alive = false;
    };
  }, [refresh]);

  const sm = snap?.self_model || {};
  const agents = snap?.agents || [];

  const agentTree = useMemo(() => {
    const byParent: Record<string, Agent[]> = {};
    for (const a of agents) {
      const p = a.parent_agent_id || "ELIAN";
      (byParent[p] ||= []).push(a);
    }
    const walk = (pid: string, depth: number): JSX.Element[] =>
      (byParent[pid] || []).flatMap((a) => {
        const cls =
          a.status === "WORKING"
            ? "ok"
            : a.status === "FAILED"
              ? "err"
              : a.status === "IDLE"
                ? "idle"
                : "warn";
        return [
          <div key={a.agent_id} style={{ marginLeft: depth * 12 }}>
            <span className={`dot ${cls}`} />
            {a.agent_type} · {a.role}{" "}
            <span className="muted">
              [{a.status}]{a.persistent ? " persistent" : " temp"}
            </span>
            {a.task ? <div className="muted">{a.task}</div> : null}
          </div>,
          ...walk(a.agent_id, depth + 1),
        ];
      });
    return (
      <div>
        <div>
          <span className="dot ok" />
          ELIAN
        </div>
        {walk("ELIAN", 1)}
        {walk("", 1)}
      </div>
    );
  }, [agents]);

  const sendChat = async () => {
    const res = await api("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: chat }),
    });
    setChatOut(JSON.stringify(res, null, 2));
    await refresh();
  };

  const selfdev = async (command: string) => {
    await api("/api/selfdev", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command }),
    });
    await refresh();
  };

  const body = (() => {
    const dev = sm.development || {};
    if (tab === "Chat") {
      return (
        <div className="card">
          <h3>Chat</h3>
          <div className="muted">One Elian personality · real backend state</div>
          <div>Focus: {sm.current_focus || "—"}</div>
          <div>
            Voice: {sm.voice_state || "IDLE"} · {sm.voice_engine || "fallback"}
          </div>
          <textarea
            rows={3}
            value={chat}
            onChange={(e) => setChat(e.target.value)}
            placeholder="Message Elian…"
          />
          <div style={{ marginTop: 8 }}>
            <button className="act" onClick={sendChat}>
              Send
            </button>
          </div>
          <pre>{chatOut}</pre>
        </div>
      );
    }
    if (tab === "Memory") {
      return (
        <div className="card">
          <h3>Memory</h3>
          <pre>{JSON.stringify(snap?.memory || {}, null, 2)}</pre>
        </div>
      );
    }
    if (tab === "Agents") {
      return (
        <>
          <div className="card">
            <h3>Agents</h3>
            {agentTree}
          </div>
          <div className="card">
            <h3>Details</h3>
            <pre>{JSON.stringify(agents, null, 2)}</pre>
          </div>
        </>
      );
    }
    if (tab === "Self-Development") {
      return (
        <div className="card">
          <h3>Self-Development</h3>
          <div>Cycle: {dev.cycle || 0}</div>
          <div>Phase: {dev.phase || "—"}</div>
          <div>Candidate: {dev.candidate || "—"}</div>
          <div>Active: {dev.active ? "yes" : "no"}</div>
          <div>Last improvement: {dev.last_improvement || "—"}</div>
          <div>Last rollback: {dev.last_rollback || "—"}</div>
          <div style={{ marginTop: 10 }}>
            <button className="act" onClick={() => selfdev("start")}>
              Start
            </button>
            <button className="act" onClick={() => selfdev("pause")}>
              Pause
            </button>
            <button className="act" onClick={() => selfdev("resume")}>
              Resume
            </button>
            <button className="act" onClick={() => selfdev("stop")}>
              Stop
            </button>
          </div>
        </div>
      );
    }
    if (tab === "Tools") {
      return (
        <div className="card">
          <h3>Tools</h3>
          <div>Installed: {(sm.installed_tools || []).join(", ") || "—"}</div>
          <div>Disabled: {(sm.disabled_tools || []).join(", ") || "—"}</div>
          <div>
            Capabilities: {(sm.available_capabilities || []).slice(0, 40).join(", ")}
          </div>
        </div>
      );
    }
    if (tab === "Tasks") {
      return (
        <div className="card">
          <h3>Tasks</h3>
          <pre>{JSON.stringify(sm.active_tasks || [], null, 2)}</pre>
          <div className="muted">Queue: {snap?.queue_len || 0}</div>
        </div>
      );
    }
    if (tab === "Automations") {
      return (
        <div className="card">
          <h3>Automations</h3>
          <div className="muted">Proactive / reminder watchers via ElianRuntime.</div>
          <div>Limitations: {(sm.known_limitations || []).join("; ") || "—"}</div>
        </div>
      );
    }
    if (tab === "System") {
      return (
        <div className="card">
          <h3>System</h3>
          <pre>
            {JSON.stringify(
              {
                health: sm.health,
                mode: sm.mode,
                language: sm.language,
                resources: snap?.resources,
                runtime: sm.runtime_state,
              },
              null,
              2
            )}
          </pre>
          <div className="muted">Self description</div>
          <pre>{snap?.describe || ""}</pre>
        </div>
      );
    }
    return (
      <div className="card">
        <h3>System Map</h3>
        {agentTree}
        <pre style={{ marginTop: 12 }}>
{`Elian
├── Conversation
├── Memory
├── Planning / Tools (dynamic)
├── Proactivity
├── Self-Development (+ workers)
└── Voice`}
        </pre>
      </div>
    );
  })();

  return (
    <>
      <header>
        <div>
          <div className="brand">ELIAN</div>
          <div className="sub">Local Control Center · React · multi-agent</div>
        </div>
        <div className="muted">
          {sm.health || "?"} · {sm.mode || "?"} · agents {agents.length}
        </div>
      </header>
      <div className="layout">
        <nav>
          {TABS.map((t) => (
            <button
              key={t}
              className={tab === t ? "active" : ""}
              onClick={() => setTab(t)}
            >
              {t}
            </button>
          ))}
        </nav>
        <main>{body}</main>
        <aside>
          <div className="card">
            <div className="muted">Avatar</div>
            <div
              className={`avatar ${avatarClass(snap?.avatar?.state || sm.voice_state)}`}
            />
            <div className="muted" style={{ textAlign: "center" }}>
              {snap?.avatar?.state || sm.voice_state || "IDLE"}
            </div>
          </div>
          <div className="card">
            <div className="muted">Live events</div>
            <pre style={{ maxHeight: 280, overflow: "auto" }}>
              {events.join("\n")}
            </pre>
          </div>
        </aside>
      </div>
    </>
  );
}
