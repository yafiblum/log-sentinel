"""
server.py: שרת FastAPI ("sidecar") שמריץ את האייג'נט בשביל אפליקציית ה-Desktop.

מה זה sidecar?
    תוכנה קטנה שרצה "בצד" של האפליקציה הראשית. Tauri מפעיל את server.exe כשהאפליקציה
    נפתחת, וסוגר אותו כשהיא נסגרת. ממשק ה-React מדבר איתו ב-HTTP על 127.0.0.1:8765.

העיקרון:
    כל הלוגיקה נשארת ב-agent/. השרת רק "מתרגם" את הגרף ל-HTTP, בדיוק כמו ש-cli.py
    מתרגם אותו לטרמינל. אותו גרף, אותם נודים, שני ממשקים.

Streaming (SSE = Server-Sent Events):
    /run ו-/resume לא מחזירים תשובה אחת בסוף. הם שולחים אירועים תוך כדי ריצה:
        data: {"type": "node_start", "name": "classify"}
        data: {"type": "node_end", "name": "classify", "took": 3.2, "detail": "CRITICAL=1 ..."}
        data: {"type": "approval_required", "thread_id": "...", "email": {...}}
        data: {"type": "done", "usage": [...], ...}
    כך ה-UI מציג התקדמות בזמן אמת, כמו "[INFO] Entering Node" ב-CLI.

הרצה בפיתוח (מתיקיית הפרויקט):
    .venv\\Scripts\\python server.py
"""

import json
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

# ------------------------------------------------------------------
# תיקיית עבודה: ב-exe (בתוך האפליקציה) עובדים מ-%APPDATA%\LogSentinel,
# כי תיקיית ההתקנה לא ניתנת לכתיבה. שם נשמרים .env, state.json, logs ו-outbox.
# חייב לקרות *לפני* ה-import של agent, כי המודולים שם קוראים את Path.cwd() כבר בטעינה.
# ------------------------------------------------------------------
if getattr(sys, "frozen", False):
    # server.exe נבנה בלי חלון קונסולה (--noconsole), ואז אין לו stdout/stderr (הם None).
    # ספריות שמנסות להדפיס היו קורסות, אז מפנים את ההדפסות ל"חור שחור" (devnull)
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
    home = Path(os.environ.get("APPDATA", Path.home())) / "LogSentinel"
    home.mkdir(parents=True, exist_ok=True)
    os.chdir(home)

import uvicorn  # noqa: E402
from dotenv import set_key  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import PlainTextResponse, StreamingResponse  # noqa: E402
from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402
from langgraph.types import Command  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from agent import __version__, llm, tools  # noqa: E402
from agent import state as file_state  # noqa: E402
from agent.cli import SUMMARY_PROMPT, node_detail  # noqa: E402  (שימוש חוזר בקוד של ה-CLI)
from agent.graph import build_graph  # noqa: E402
from agent.llm import AgentError  # noqa: E402
from agent.logger import LOG_FILE, log  # noqa: E402

PORT = 8765

app = FastAPI(title="LogSentinel sidecar", version=__version__)

# CORS: הדפדפן (ה-WebView של Tauri) חוסם בקשות לכתובת אחרת, אלא אם השרת מתיר במפורש
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:1420", "tauri://localhost", "http://tauri.localhost", "https://tauri.localhost"],
    allow_methods=["*"],
    allow_headers=["*"],
)

graph = build_graph()

# משימות שמחכות לאישור (HITL): thread_id -> מידע על המשימה
pending: dict[str, dict] = {}
# היסטוריית הסשן (בשביל תקציר ועלות מצטברת)
session_history: list[dict] = []


# ==================================================================
# 1. הרצת הגרף עם Streaming
# ==================================================================
def sse(event: dict) -> str:
    """אירוע SSE אחד: שורת data עם JSON, ושורה ריקה בסוף."""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def run_stream(graph_input, task: dict):
    """
    מריץ את הגרף (או ממשיך אותו אחרי HITL) ומחזיר אירועים תוך כדי ריצה.
    task מחזיק את ה-config (עם thread_id) ואת מה שנאסף עד עכשיו.
    """
    config = task["config"]
    node_started: dict[str, float] = {}
    interrupt_value = None
    try:
        for mode, chunk in graph.stream(graph_input, config, stream_mode=["tasks", "updates"]):
            if mode == "tasks":
                if "input" in chunk:  # נוד מתחיל
                    node_started[chunk["name"]] = time.time()
                    yield sse({"type": "node_start", "name": chunk["name"]})
                continue
            for name, update in chunk.items():
                if name == "__interrupt__":
                    interrupt_value = update[0].value
                    continue
                took = round(time.time() - node_started.get(name, time.time()), 1)
                yield sse({"type": "node_end", "name": name, "took": took, "detail": node_detail(name, update)})
                if update and "answer" in update:
                    task["answer"] = update["answer"]
                    yield sse({"type": "answer", "text": update["answer"]})
                if update and "email_status" in update:
                    task["email_status"] = update["email_status"]
                    yield sse({"type": "email_status", "text": update["email_status"]})

        if interrupt_value is not None:
            # HITL: הגרף עצר ב-human_approve. שומרים את המשימה ומבקשים מה-UI אישור
            thread_id = config["configurable"]["thread_id"]
            pending[thread_id] = task
            yield sse({"type": "approval_required", "thread_id": thread_id, "email": interrupt_value["email"]})
            return

        yield sse(finish(task))

    except AgentError as e:
        log.error(f"Task failed: {str(e)[:200]}")
        yield sse({"type": "error", "message": str(e)})
        yield sse(finish(task, failed=True))
    except Exception as e:  # שגיאה לא צפויה: נרשמת ללוג המלא, וה-UI מקבל הודעה ברורה
        log.exception("Unexpected error in server task")
        yield sse({"type": "error", "message": f"שגיאה לא צפויה: {type(e).__name__}: {e}\nפרטים מלאים בקובץ {LOG_FILE}"})
        yield sse(finish(task, failed=True))


def finish(task: dict, failed: bool = False) -> dict:
    """אירוע הסיום: טבלת עלויות של המשימה + רישום להיסטוריה."""
    rows = list(llm.usage_log)
    total = {
        "calls": len(rows),
        "input_tokens": sum(r["input_tokens"] for r in rows),
        "output_tokens": sum(r["output_tokens"] for r in rows),
        "actual_cost": sum(r["actual_cost"] for r in rows),
        "theoretical_cost": sum(r["theoretical_cost"] for r in rows),
    }
    elapsed = round(time.time() - task["start"], 1)
    session_history.append({
        "time": datetime.now().strftime("%H:%M"),
        "input": task["input"],
        "answer": task.get("answer", "") if not failed else "(נכשל)",
        "email_status": task.get("email_status"),
        "cost": total["theoretical_cost"],
        "tokens": total["input_tokens"] + total["output_tokens"],
    })
    log.info(f"Task finished in {elapsed}s calls={total['calls']} failed={failed}")
    return {"type": "done", "usage": rows, "total": total, "elapsed": elapsed, "failed": failed}


class RunRequest(BaseModel):
    input: str


class ResumeRequest(BaseModel):
    thread_id: str
    approved: bool


STREAM_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@app.post("/run")
def run(req: RunRequest):
    """הפעלת משימה חדשה. מחזיר Stream של אירועים."""
    text = req.input.strip()
    if not text:
        raise HTTPException(400, "הבקשה ריקה")
    llm.reset_usage()
    log.info(f"Task started (desktop): {text[:120]!r}")
    task = {
        "input": text,
        "start": time.time(),
        "config": {
            "configurable": {"thread_id": str(uuid.uuid4())},
            "run_name": "LogSentinel",
            "tags": ["desktop"],
            "metadata": {"model": llm.DEFAULT_MODEL, "user_input": text[:200]},
        },
    }
    return StreamingResponse(run_stream({"user_input": text}, task),
                             media_type="text/event-stream", headers=STREAM_HEADERS)


@app.post("/resume")
def resume(req: ResumeRequest):
    """המשך הגרף אחרי HITL, עם התשובה של המשתמשת (שלח / בטל)."""
    task = pending.pop(req.thread_id, None)
    if task is None:
        raise HTTPException(404, "המשימה לא נמצאה (אולי כבר טופלה)")
    log.info(f"HITL answer from desktop: approved={req.approved}")
    return StreamingResponse(run_stream(Command(resume=req.approved), task),
                             media_type="text/event-stream", headers=STREAM_HEADERS)


# ==================================================================
# 2. כלים, הגדרות ומידע
# ==================================================================
@app.get("/health")
def health():
    return {"ok": True, "version": __version__, "model": llm.DEFAULT_MODEL, "cwd": str(Path.cwd())}


@app.get("/tools")
def get_tools():
    """רשימת הכלים וההרחבות ומצב כל אחד (פאנל "כלים" בצד ה-UI)."""
    email = tools.email_settings()
    has_key = bool(os.getenv("OPENROUTER_API_KEY"))
    return [
        {"name": "openrouter", "title": "OpenRouter (LLM)", "status": "ok" if has_key else "missing",
         "detail": llm.DEFAULT_MODEL if has_key else "חסר מפתח API", "configurable": True,
         "fields": [{"key": "OPENROUTER_API_KEY", "label": "API Key", "secret": True, "set": has_key}]},
        {"name": "web_search", "title": "Web Search (Stack Overflow)", "status": "ok",
         "detail": "חיפוש תשובות לשגיאות. ללא מפתח, עד 300 ביום", "configurable": False},
        {"name": "email", "title": "Email (SMTP)", "status": "ok" if email["mode"] == "real" else "test",
         "detail": f"נמען: {email['to']}" if email["mode"] == "real" else "מצב בדיקה: נשמר ל-outbox",
         "configurable": True,
         "fields": [
             {"key": "SMTP_HOST", "label": "שרת SMTP", "secret": False, "value": email["host"]},
             {"key": "SMTP_PORT", "label": "פורט", "secret": False, "value": str(email["port"])},
             {"key": "SMTP_USER", "label": "כתובת השולח", "secret": False, "value": email["user"]},
             {"key": "SMTP_PASSWORD", "label": "סיסמת אפליקציה", "secret": True, "set": bool(email["password"])},
             {"key": "ALERT_EMAIL_TO", "label": "למי לשלוח התראות", "secret": False, "value": email["to"]},
         ]},
        {"name": "langsmith", "title": "LangSmith (ניטור)", "status": "ok" if llm.langsmith_enabled() else "off",
         "detail": f"project: {os.getenv('LANGSMITH_PROJECT', '-')}" if llm.langsmith_enabled() else "כבוי",
         "configurable": False},
        {"name": "files", "title": "File System", "status": "ok",
         "detail": "קריאה בלבד של קבצי לוג", "configurable": False},
    ]


# אילו מפתחות מותר לעדכן דרך ה-API (כדי שלא יהיה אפשר לשנות משתני סביבה אחרים)
AUTH_KEYS = {
    "openrouter": {"OPENROUTER_API_KEY"},
    "email": {"SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "ALERT_EMAIL_TO"},
}


class AuthRequest(BaseModel):
    values: dict[str, str]
    save: bool = True


@app.post("/tools/{name}/auth")
def tool_auth(name: str, req: AuthRequest):
    """שמירת פרטי אוטנטיקציה לכלי. נשמר ב-.env המקומי בלבד. הערכים לא נרשמים בלוג."""
    allowed = AUTH_KEYS.get(name)
    if not allowed:
        raise HTTPException(404, f"לכלי '{name}' אין הגדרות")
    values = {k: v.strip() for k, v in req.values.items() if k in allowed and v.strip()}
    for k, v in values.items():
        os.environ[k] = v
    if req.save and values:
        env_path = Path.cwd() / ".env"
        env_path.touch(exist_ok=True)
        for k, v in values.items():
            set_key(str(env_path), k, v)
    log.info(f"tool auth updated tool={name} keys={list(values)} (values not logged)")
    return {"ok": True, "updated": list(values)}


@app.get("/status")
def status():
    """הקבצים שכבר נסרקו (מתוך state.json)."""
    return [{"file": path, "lines": e.get("lines", 0), "mtime": e["mtime"]} for path, e in file_state.load_state().items()]


@app.get("/usage")
def usage():
    """עלויות ושימוש מצטברים בסשן."""
    return {
        "tasks": len(session_history),
        "theoretical_cost": sum(h["cost"] for h in session_history),
        "tokens": sum(h["tokens"] for h in session_history),
    }


@app.get("/models")
def models():
    return {"current": llm.DEFAULT_MODEL, "free": llm.list_free_models(),
            "suggested_paid": ["openai/gpt-4o-mini", "openai/gpt-4.1-mini"]}


class ModelRequest(BaseModel):
    name: str


@app.post("/model")
def set_model(req: ModelRequest):
    try:
        llm.set_model(req.name.strip())
    except AgentError as e:
        raise HTTPException(400, str(e))
    return {"current": llm.DEFAULT_MODEL}


@app.get("/summary")
def summary():
    """[בונוס] תקציר שיחה כקובץ טקסט להורדה."""
    if not session_history:
        raise HTTPException(400, "עדיין אין מה לסכם. הריצי קודם משימה.")
    transcript = "\n\n".join(
        f"[{h['time']}] USER: {h['input']}\nAGENT: {h['answer'][:3000]}\n{h.get('email_status') or ''}"
        for h in session_history)
    llm.reset_usage()
    try:
        text = llm.call_llm("summary", [SystemMessage(content=SUMMARY_PROMPT),
                                        HumanMessage(content=transcript)]).content.strip()
    except AgentError as e:
        raise HTTPException(502, str(e))
    total_cost = sum(h["cost"] for h in session_history)
    body = (f"LogSentinel - תקציר שיחה\n{datetime.now():%d/%m/%Y %H:%M}\n"
            f"מודל: {llm.DEFAULT_MODEL} | משימות: {len(session_history)} | עלות תיאורטית: ${total_cost:.4f}\n"
            f"{'=' * 60}\n\n{text}\n\n{'=' * 60}\nתמליל מלא:\n\n{transcript}\n")
    filename = f"logsentinel_summary_{datetime.now():%Y%m%d_%H%M%S}.txt"
    return PlainTextResponse(body, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def watch_parent(pid: int) -> None:
    """
    "כלב שמירה": מחכה שהאפליקציה (Tauri) תיסגר, ואז סוגר את השרת.
    למה צריך? server.exe של PyInstaller הוא בעצם שני תהליכים (מפעיל + התוכנה עצמה).
    כש-Tauri סוגר את המפעיל, התוכנה עצמה הייתה נשארת לרוץ ברקע. כך היא נסגרת לבד,
    גם אם האפליקציה קרסה.
    """
    import ctypes
    import threading

    SYNCHRONIZE, INFINITE = 0x00100000, 0xFFFFFFFF
    handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        return  # לא הצלחנו לפתוח את התהליך: ממשיכים בלי שמירה

    def wait_and_exit():
        ctypes.windll.kernel32.WaitForSingleObject(handle, INFINITE)  # חוסם עד שהאפליקציה נסגרת
        log.info("Parent app exited, shutting down sidecar")
        os._exit(0)

    threading.Thread(target=wait_and_exit, daemon=True).start()


if __name__ == "__main__":
    # Tauri מעביר את ה-PID שלו: server.exe --parent-pid 1234
    if "--parent-pid" in sys.argv and sys.platform == "win32":
        watch_parent(int(sys.argv[sys.argv.index("--parent-pid") + 1]))
    log.info(f"Sidecar starting on 127.0.0.1:{PORT} cwd={Path.cwd()}")
    # 127.0.0.1 בלבד: השרת לא נגיש ממחשבים אחרים ברשת
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
