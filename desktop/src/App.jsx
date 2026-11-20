// App.jsx: המסך הראשי של LogSentinel Desktop.
//
// מבנה המסך:
//   ┌ Header: שם, בחירת מודל, עזרה, תקציר ───────────────────┐
//   │ Sidebar: כלים (+הגדרה), קבצים שנסרקו, עלות │ צ'אט        │
//   │                                             │ אזור גרירה  │
//   └─────────────────────────────────────────────┴─────────────┘
//
// מושג חשוב ב-React: state.
//   useState מחזיק נתון שכשהוא משתנה, React מצייר מחדש את מה שתלוי בו.
//   למשל: כשמגיע אירוע node_end מהשרת, מעדכנים את רשימת הנודים, והמסך מתעדכן לבד.

import { useEffect, useRef, useState } from "react";
import { getJSON, postJSON, stream, waitForServer, SERVER } from "./api.js";
import { HelpModal, ApprovalModal, AuthModal } from "./Modals.jsx";

// האם רצים בתוך Tauri (אפליקציה) או בדפדפן רגיל (פיתוח)?
const IN_TAURI = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

const STATUS_DOT = { ok: "🟢", test: "🟡", missing: "🔴", off: "⚪" };

export default function App() {
  const [server, setServer] = useState(null);       // מידע מ-/health (null = עדיין עולה)
  const [bootError, setBootError] = useState("");
  const [tasks, setTasks] = useState([]);            // כל המשימות בשיחה
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [toolsList, setToolsList] = useState([]);
  const [scanned, setScanned] = useState([]);
  const [usage, setUsage] = useState({ tasks: 0, theoretical_cost: 0, tokens: 0 });
  const [models, setModels] = useState({ current: "", free: [], suggested_paid: [] });
  const [approval, setApproval] = useState(null);    // טיוטת מייל שמחכה לאישור
  const [modal, setModal] = useState(null);          // "help" | {auth: tool}
  const [dragOver, setDragOver] = useState(false);
  const chatEnd = useRef(null);

  // --- עלייה: מחכים לשרת, ואז טוענים את הנתונים לצד ---
  useEffect(() => {
    waitForServer().then((h) => { setServer(h); refreshSide(); }).catch((e) => setBootError(e.message));
  }, []);

  // גלילה אוטומטית לסוף הצ'אט
  useEffect(() => { chatEnd.current?.scrollIntoView({ behavior: "smooth" }); }, [tasks]);

  // --- גרירת תיקייה: Tauri נותן את הנתיב המלא של מה שנגרר לחלון ---
  useEffect(() => {
    if (!IN_TAURI) return;
    let unlisten;
    import("@tauri-apps/api/webview").then(({ getCurrentWebview }) =>
      getCurrentWebview().onDragDropEvent((e) => {
        const t = e.payload.type;
        if (t === "enter" || t === "over") setDragOver(true);
        else setDragOver(false);
        if (t === "drop" && e.payload.paths?.length) setInput(`scan ${e.payload.paths[0]}`);
      }).then((fn) => (unlisten = fn)));
    return () => unlisten?.();
  }, []);

  async function refreshSide() {
    try {
      const [t, s, u, m] = await Promise.all([getJSON("/tools"), getJSON("/status"), getJSON("/usage"), getJSON("/models")]);
      setToolsList(t); setScanned(s); setUsage(u); setModels(m);
    } catch { /* השרת עוד לא מוכן, ננסה בפעם הבאה */ }
  }

  // עדכון המשימה האחרונה ברשימה (immutable: יוצרים עותק, כך React יודע שמשהו השתנה)
  const updateLast = (fn) => setTasks((all) => all.map((t, i) => (i === all.length - 1 ? fn(t) : t)));

  // --- טיפול באירועים שמגיעים מהשרת בזמן ריצה ---
  function onEvent(ev) {
    switch (ev.type) {
      case "node_start":
        updateLast((t) => ({ ...t, nodes: [...t.nodes, { name: ev.name, running: true }] }));
        break;
      case "node_end":
        updateLast((t) => ({
          ...t,
          nodes: t.nodes.map((n) => (n.name === ev.name && n.running ? { ...n, running: false, took: ev.took, detail: ev.detail } : n)),
        }));
        break;
      case "answer":
        updateLast((t) => ({ ...t, answer: ev.text }));
        break;
      case "email_status":
        updateLast((t) => ({ ...t, emailStatus: ev.text }));
        break;
      case "error":
        updateLast((t) => ({ ...t, error: ev.message }));
        break;
      case "approval_required":
        setApproval({ threadId: ev.thread_id, email: ev.email });   // פותח את חלון האישור
        break;
      case "done":
        updateLast((t) => ({ ...t, done: true, usage: ev.usage, total: ev.total, elapsed: ev.elapsed,
                             nodes: t.nodes.map((n) => ({ ...n, running: false })) }));
        setBusy(false);
        refreshSide();
        break;
    }
  }

  async function send(text) {
    const value = (text ?? input).trim();
    if (!value || busy) return;
    const lower = value.toLowerCase();
    if (lower === "help" || value === "?") { setModal("help"); setInput(""); return; }
    setInput("");
    setBusy(true);
    setTasks((all) => [...all, { input: value, nodes: [], answer: "", error: "", done: false }]);
    try {
      await stream("/run", { input: value }, onEvent);
    } catch (e) {
      updateLast((t) => ({ ...t, error: e.message, done: true }));
      setBusy(false);
    }
  }

  // HITL: המשתמשת לחצה "שלח" או "בטל" בחלון האישור
  async function answerApproval(approved) {
    const { threadId } = approval;
    setApproval(null);
    try {
      await stream("/resume", { thread_id: threadId, approved }, onEvent);
    } catch (e) {
      updateLast((t) => ({ ...t, error: e.message, done: true }));
      setBusy(false);
    }
  }

  async function pickFolder() {
    if (!IN_TAURI) return;
    const { open } = await import("@tauri-apps/plugin-dialog");
    const dir = await open({ directory: true, title: "בחרי תיקיית לוגים" });
    if (dir) setInput(`scan ${dir}`);
  }

  async function changeModel(name) {
    try { const r = await postJSON("/model", { name }); setModels((m) => ({ ...m, current: r.current })); refreshSide(); }
    catch (e) { alert(e.message); }
  }

  async function downloadSummary() {
    try {
      const res = await fetch(SERVER + "/summary");
      if (!res.ok) throw new Error((await res.json()).detail);
      const text = await res.text();
      const name = res.headers.get("Content-Disposition")?.match(/filename="(.+)"/)?.[1] || "summary.txt";
      if (IN_TAURI) {
        // באפליקציה: חלון "שמירה בשם" ושמירה לדיסק דרך Tauri
        const { save } = await import("@tauri-apps/plugin-dialog");
        const { invoke } = await import("@tauri-apps/api/core");
        const path = await save({ defaultPath: name, filters: [{ name: "Text", extensions: ["txt"] }] });
        if (path) { await invoke("save_text_file", { path, contents: text }); alert(`💾 נשמר: ${path}`); }
      } else {
        const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(new Blob([text])), download: name });
        a.click();
      }
    } catch (e) { alert(e.message); }
  }

  // --- מסך טעינה ---
  if (!server) {
    return (
      <div className="boot">
        <div className="boot-logo">🛡️</div>
        <h1>LogSentinel</h1>
        {bootError ? <div className="error-box">{bootError}</div> : <p className="muted">מפעיל את האייג'נט...</p>}
      </div>
    );
  }

  return (
    <div className="app">
      <header className="header">
        <div className="brand">🛡️ LogSentinel <span className="version">v{server.version}</span></div>
        <div className="header-actions">
          <select className="model-select" value={models.current} onChange={(e) => changeModel(e.target.value)} title="מודל">
            {[...new Set([models.current, ...models.free, ...models.suggested_paid])].filter(Boolean).map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
          <button className="btn ghost" onClick={downloadSummary} disabled={!usage.tasks}>📄 תקציר שיחה</button>
          <button className="btn ghost icon" onClick={() => setModal("help")} title="עזרה">❓</button>
        </div>
      </header>

      <aside className="sidebar">
        <section>
          <h3>🔌 כלים וחיבורים</h3>
          {toolsList.map((t) => (
            <div key={t.name} className="tool">
              <div className="tool-row">
                <span>{STATUS_DOT[t.status]} {t.title}</span>
                {t.configurable && <button className="btn tiny" onClick={() => setModal({ auth: t })}>הגדר</button>}
              </div>
              <div className="tool-detail">{t.detail}</div>
            </div>
          ))}
        </section>

        <section>
          <h3>📁 קבצים שנסרקו</h3>
          {scanned.length === 0 && <div className="muted small">עדיין לא נסרקו קבצים</div>}
          {scanned.map((f) => (
            <div key={f.file} className="scanned" title={f.file}>
              <span>{f.file.split(/[\\/]/).pop()}</span><span className="muted">{f.lines.toLocaleString()} שורות</span>
            </div>
          ))}
        </section>

        <section>
          <h3>💰 עלות בסשן</h3>
          <div className="cost-big">${usage.theoretical_cost.toFixed(4)}</div>
          <div className="muted small">{usage.tokens.toLocaleString()} טוקנים · {usage.tasks} משימות</div>
          <div className="muted small">עלות תיאורטית (במודל חינמי בפועל $0)</div>
        </section>
      </aside>

      <main className="main">
        <div className="chat">
          {tasks.length === 0 && (
            <div className="empty">
              <p>גררי לכאן תיקיית לוגים, או כתבי בקשה. למשל:</p>
              {["scan evals/sample_logs --all", "מה המשמעות של ECONNREFUSED?", "Why does nginx return 502 Bad Gateway?"].map((ex) => (
                <button key={ex} className="example" onClick={() => setInput(ex)}>{ex}</button>
              ))}
            </div>
          )}
          {tasks.map((t, i) => <TaskView key={i} task={t} />)}
          <div ref={chatEnd} />
        </div>

        <div className={`dropzone ${dragOver ? "over" : ""}`} onClick={pickFolder}>
          📂 {dragOver ? "שחררי כאן" : IN_TAURI ? "גררי לכאן תיקייה או קובץ לוג, או לחצי לבחירה" : "גרירה זמינה באפליקציה (Tauri)"}
        </div>

        <form className="composer" onSubmit={(e) => { e.preventDefault(); send(); }}>
          <input value={input} onChange={(e) => setInput(e.target.value)} disabled={busy}
                 placeholder={busy ? "האייג'נט עובד..." : "כתבי בקשה... (scan <תיקייה> או שאלה על שגיאה)"} />
          <button className="btn primary" disabled={busy || !input.trim()}>{busy ? "⏳" : "שלח"}</button>
        </form>
      </main>

      {modal === "help" && <HelpModal onClose={() => setModal(null)} />}
      {modal?.auth && <AuthModal tool={modal.auth} onClose={() => { setModal(null); refreshSide(); }} />}
      {approval && <ApprovalModal email={approval.email} onAnswer={answerApproval} />}
    </div>
  );
}

// --- משימה אחת בצ'אט: בקשה, התקדמות הנודים, תשובה, שגיאה ועלות ---
function TaskView({ task }) {
  const [showUsage, setShowUsage] = useState(false);
  return (
    <div className="task">
      <div className="bubble user">{task.input}</div>

      <div className="progress">
        {task.nodes.map((n, i) => (
          <span key={i} className={`node ${n.running ? "running" : "finished"}`} title={n.detail || ""}>
            {n.running ? "⏳" : "✓"} {n.name}{n.took !== undefined && <small> {n.took}s</small>}
            {n.detail && !n.running && <em> · {n.detail}</em>}
          </span>
        ))}
      </div>

      {task.answer && <div className="bubble agent"><pre>{task.answer}</pre></div>}
      {task.emailStatus && <div className="email-status">{task.emailStatus}</div>}
      {task.error && <div className="error-box"><strong>❌ שגיאה</strong><pre>{task.error}</pre></div>}

      {task.done && task.total && (
        <div className="usage">
          <button className="link" onClick={() => setShowUsage(!showUsage)}>
            💰 {task.total.calls} קריאות · {(task.total.input_tokens + task.total.output_tokens).toLocaleString()} טוקנים ·
            בפועל ${task.total.actual_cost.toFixed(6)} · תיאורטי ${task.total.theoretical_cost.toFixed(6)} · ⏱️ {task.elapsed}s
            {task.total.calls > 0 && (showUsage ? " ▲" : " ▼")}
          </button>
          {showUsage && task.usage.length > 0 && (
            <table>
              <thead><tr><th>Node</th><th>Model</th><th>In</th><th>Out</th><th>Actual $</th><th>Theoretical $</th></tr></thead>
              <tbody>
                {task.usage.map((u, i) => (
                  <tr key={i}><td>{u.node}</td><td>{u.model}</td><td>{u.input_tokens}</td><td>{u.output_tokens}</td>
                    <td>{u.actual_cost.toFixed(6)}</td><td>{u.theoretical_cost.toFixed(6)}</td></tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}
