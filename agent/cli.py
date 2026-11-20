"""
cli.py: נקודת הכניסה (entrypoint) של ממשק שורת הפקודה.

הרצה (מתיקיית הפרויקט):
    .venv\\Scripts\\python -m agent.cli                  ← מצב אינטראקטיבי
    .venv\\Scripts\\python -m agent.cli --ask "שאלה"     ← בקשה אחת ויציאה

מבנה הקובץ:
    1. פקודות מערכת (help, status, tools, model, config email, summary) - בלי הגרף
    2. run_task - מריץ את הגרף עם Streaming, HITL, טבלת עלויות ושגיאות
    3. main - הלולאה האינטראקטיבית שמחליטה לאן כל קלט הולך
"""

import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

from dotenv import set_key
from langgraph.types import Command
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from agent import __version__
from agent import llm, tools
from agent import state as file_state
from agent.graph import build_graph
from agent.llm import AgentError, usage_log, reset_usage
from agent.logger import log, LOG_FILE

# בטרמינל של Windows צריך להגדיר UTF-8, אחרת עברית לא מודפסת
sys.stdout.reconfigure(encoding="utf-8")

console = Console()

# היסטוריית הסשן: כל משימה שבוצעה (בשביל summary ועלות מצטברת)
session_history: list[dict] = []


# ==================================================================
# 1. פקודות מערכת
# ==================================================================
HELP_TEXT = """[bold]🛡️  LogSentinel: אייג'נט לניתוח לוגים והתראה על תקלות[/bold]

[bold cyan]סריקת לוגים[/bold cyan]
  scan <תיקייה>           סריקת קבצי לוג (רק מה שחדש מאז הסריקה הקודמת)
  scan <תיקייה> --all     סריקה מחדש של הכל
  analyze <קובץ>          ניתוח קובץ לוג בודד
  [dim]אפשר גם בשפה חופשית: "תבדקי את הלוגים ב-C:\\logs"[/dim]

[bold cyan]שאלות[/bold cyan]
  <שאלה על שגיאה>         למשל: מה זה ECONNREFUSED?  (כולל חיפוש ב-Stack Overflow)

[bold cyan]מערכת[/bold cyan]
  status                 אילו קבצים כבר נסרקו
  tools                  הרחבות וחיבורים פעילים (חיפוש, מייל, LangSmith)
  config email           הגדרת פרטי מייל להתראות
  model                  המודל הנוכחי + רשימת מודלים חינמיים
  model <שם>             החלפת מודל
  summary                ייצוא תקציר השיחה לקובץ טקסט
  help / ?               המסך הזה
  exit                   יציאה

[bold cyan]איך זה עובד[/bold cyan]
  guardrail → router → scan → filter → classify → analyze ⇄ evaluator → report
  → אם יש תקלה CRITICAL: ניסוח מייל ובקשת אישור [Y/N] לפני שליחה"""


def cmd_help() -> None:
    console.print(Panel(HELP_TEXT, border_style="cyan"))


def cmd_status() -> None:
    """מציג את state.json: אילו קבצים נסרקו ועד איפה."""
    saved = file_state.load_state()
    if not saved:
        console.print("עדיין לא נסרקו קבצים. נסי: scan <תיקייה>\n")
        return
    table = Table(title="📂 קבצים שנסרקו (state.json)", title_justify="left")
    table.add_column("קובץ")
    table.add_column("שורות שנקראו", justify="right")
    table.add_column("שינוי אחרון בקובץ")
    for path, entry in saved.items():
        changed = datetime.fromtimestamp(entry["mtime"]).strftime("%d/%m/%Y %H:%M")
        table.add_row(path, f"{entry.get('lines', 0):,}", changed)
    console.print(table)
    console.print()


def cmd_tools() -> None:
    """מציג את ההרחבות והחיבורים, ומצב כל אחד מהם."""
    email = tools.email_settings()
    table = Table(title="🔌 הרחבות וחיבורים", title_justify="left")
    table.add_column("רכיב")
    table.add_column("מצב")
    table.add_column("פרטים")
    table.add_row("OpenRouter (LLM)",
                  "🟢 מחובר" if os.getenv("OPENROUTER_API_KEY") else "🔴 חסר מפתח",
                  llm.DEFAULT_MODEL)
    table.add_row("Web Search (Stack Overflow)", "🟢 פעיל", "חיפוש תשובות לשגיאות. ללא מפתח, עד 300 ביום")
    table.add_row("Email",
                  "🟢 SMTP" if email["mode"] == "real" else "🟡 מצב בדיקה",
                  f"נמען: {email['to']}" if email["mode"] == "real" else "נשמר ל-outbox/. להגדרה: config email")
    table.add_row("LangSmith (ניטור)",
                  "🟢 פעיל" if llm.langsmith_enabled() else "⚪ כבוי",
                  f"project: {os.getenv('LANGSMITH_PROJECT', '-')}" if llm.langsmith_enabled()
                  else "הוסיפי LANGSMITH_API_KEY ל-.env")
    table.add_row("File System", "🟢 פעיל", "קריאה בלבד של קבצי לוג. האייג'נט לא משנה ולא מוחק קבצים")
    table.add_row("Agent log", "🟢 פעיל", str(LOG_FILE))
    console.print(table)
    console.print()


def cmd_model(arg: str) -> None:
    """בלי ארגומנט: מציג את המודל ורשימת מודלים חינמיים. עם ארגומנט: מחליף מודל."""
    if arg:
        llm.set_model(arg)
        console.print(f"✅ המודל הוחלף ל: [bold]{escape(arg)}[/bold]\n")
        return
    console.print(f"המודל הנוכחי: [bold]{escape(llm.DEFAULT_MODEL)}[/bold]")
    free = llm.list_free_models()
    if free:
        console.print("מודלים חינמיים זמינים ב-OpenRouter:")
        for m in free:
            console.print(f"  • {escape(m)}")
    console.print("[dim]להחלפה: model <שם>. אפשר גם מודל בתשלום, למשל model openai/gpt-4o-mini[/dim]\n")


def cmd_config_email() -> None:
    """
    הזנת פרטי מייל דרך הטרמינל (אוטנטיקציה ל-Tool).
    הסיסמה מוקלדת במצב מוסתר, ונשמרת רק ב-.env המקומי (שלא עולה ל-GitHub).
    """
    console.print(Panel(
        "הגדרת שליחת מיילים דרך SMTP.\n"
        "ב-Gmail: צריך 'סיסמת אפליקציה' (לא הסיסמה הרגילה): https://myaccount.google.com/apppasswords\n"
        "Enter בלי ערך = השארת הערך הקיים.",
        title="📧 config email", border_style="cyan"))

    fields = [
        ("SMTP_HOST", "שרת SMTP", False),
        ("SMTP_PORT", "פורט", False),
        ("SMTP_USER", "כתובת השולח (המשתמש)", False),
        ("SMTP_PASSWORD", "סיסמת אפליקציה", True),
        ("ALERT_EMAIL_TO", "למי לשלוח התראות", False),
    ]
    new_values = {}
    for key, label, secret in fields:
        current = os.getenv(key, "")
        shown = "••••••" if (secret and current) else current
        # password=True: ההקלדה לא מוצגת על המסך (רק בטרמינל אמיתי, לא כשהקלט מגיע מקובץ)
        hide = secret and sys.stdin.isatty()
        value = console.input(f"{label} [dim]{escape(f'[{shown}]')}[/dim]: ", password=hide).strip()
        if value:
            new_values[key] = value

    if not new_values:
        console.print("לא שונה כלום.\n")
        return

    for key, value in new_values.items():
        os.environ[key] = value                          # תקף מיד, בסשן הנוכחי
    save = console.input("לשמור גם בקובץ .env לפעמים הבאות? [Y/N]: ").strip().lower()
    if save in ("y", "yes", "כן"):
        env_path = Path.cwd() / ".env"
        env_path.touch(exist_ok=True)
        for key, value in new_values.items():
            set_key(str(env_path), key, value)            # מעדכן שורה אחת ב-.env
        console.print("💾 נשמר ב-.env")
    log.info(f"config email updated keys={list(new_values)} (values not logged)")
    console.print(f"מצב מייל: [bold]{tools.email_settings()['mode']}[/bold]\n")


SUMMARY_PROMPT = """Summarize this LogSentinel session for the user, in Hebrew, as plain text (no markdown).
Include: what was scanned or asked, the main incidents found (with severity), actions taken (emails sent/cancelled),
and recommended next steps. Be concise (max ~250 words)."""


def cmd_summary() -> None:
    """[בונוס] יוצר תקציר שיחה בעזרת AI ושומר אותו לקובץ טקסט."""
    if not session_history:
        console.print("עדיין אין מה לסכם. הריצי קודם משימה.\n")
        return

    transcript = "\n\n".join(
        f"[{h['time']}] USER: {h['input']}\nAGENT: {h['answer'][:3000]}\n{h.get('email_status') or ''}"
        for h in session_history
    )
    from langchain_core.messages import SystemMessage, HumanMessage
    reset_usage()
    with console.status("יוצר תקציר..."):
        summary = llm.call_llm("summary", [SystemMessage(content=SUMMARY_PROMPT),
                                           HumanMessage(content=transcript)]).content.strip()

    out_dir = Path.cwd() / "summaries"
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"session_{datetime.now():%Y%m%d_%H%M%S}.txt"
    total_cost = sum(h["cost"] for h in session_history)
    path.write_text(
        f"LogSentinel - תקציר שיחה\n{datetime.now():%d/%m/%Y %H:%M}\n"
        f"מודל: {llm.DEFAULT_MODEL} | משימות: {len(session_history)} | עלות תיאורטית: ${total_cost:.4f}\n"
        f"{'=' * 60}\n\n{summary}\n\n{'=' * 60}\nתמליל מלא:\n\n{transcript}\n",
        encoding="utf-8")
    console.print(Panel(escape(summary), title="📄 תקציר", border_style="green"))
    console.print(f"💾 נשמר ב: {path}\n")


# ==================================================================
# 2. הרצת משימה דרך הגרף
# ==================================================================
def node_detail(node: str, update: dict | None) -> str:
    """שורת פירוט קצרה לכל נוד שהסתיים (מה הוא מצא/החליט)."""
    u = update or {}
    if node == "guardrail":
        return "מותר" if u.get("in_scope") else f"נחסם ({u.get('block_reason')})"
    if node == "parse_request":
        return u.get("intent", "") + (f" → {u['target_path']}" if u.get("target_path") else "")
    if node == "scan_files":
        files = u.get("files_report", [])
        return f"{len(files)} קבצים, {sum(f['new_lines'] for f in files)} שורות חדשות"
    if node == "filter_errors":
        return f"{len(u.get('errors', []))} שגיאות"
    if node == "classify":
        c = u.get("classified", [])
        return " ".join(f"{s}={sum(1 for x in c if x['severity'] == s)}" for s in ("CRITICAL", "WARNING", "INFO"))
    if node == "analyze":
        return f"{len(u.get('incidents', []))} תקלות (ניסיון {u.get('analyze_attempts')})"
    if node == "evaluator":
        return f"ציון {u.get('eval_score')}/5"
    if node == "answer" and u.get("sources"):
        return f"{len(u['sources'])} מקורות מהרשת"
    return ""


def print_usage_table() -> None:
    """מדפיס טבלת טוקנים ועלויות לכל נוד שקרא ל-LLM, ושורת סיכום."""
    if not usage_log:
        console.print("[dim]💰 לא בוצעו קריאות ל-LLM במשימה הזו (עלות: $0)[/dim]\n")
        return

    table = Table(title="💰 שימוש ועלות", title_justify="left")
    table.add_column("Node")
    table.add_column("Model")
    table.add_column("Tokens in", justify="right")
    table.add_column("Tokens out", justify="right")
    table.add_column("Actual $", justify="right")
    table.add_column("Theoretical $", justify="right")

    for row in usage_log:
        table.add_row(
            row["node"], row["model"],
            f"{row['input_tokens']:,}", f"{row['output_tokens']:,}",
            f"{row['actual_cost']:.6f}", f"{row['theoretical_cost']:.6f}",
        )

    table.add_section()
    table.add_row(
        "[bold]TOTAL[/bold]", f"{len(usage_log)} calls",
        f"{sum(r['input_tokens'] for r in usage_log):,}",
        f"{sum(r['output_tokens'] for r in usage_log):,}",
        f"[bold]{sum(r['actual_cost'] for r in usage_log):.6f}[/bold]",
        f"[bold]{sum(r['theoretical_cost'] for r in usage_log):.6f}[/bold]",
    )
    console.print(table)
    console.print()


def ask_email_approval(email: dict) -> bool:
    """HITL בטרמינל: מציג את טיוטת המייל ושואל [Y/N]."""
    mode = "שליחה אמיתית (SMTP)" if email["mode"] == "real" else "מצב בדיקה: יישמר ל-outbox/ ולא יישלח"
    console.print(Panel(
        f"[bold]אל:[/bold] {escape(email['to'])}\n"
        f"[bold]נושא:[/bold] {escape(email['subject'])}\n"
        f"[dim]({mode})[/dim]\n\n{escape(email['body'])}",
        title="📧 נמצאה תקלה קריטית: טיוטת מייל התראה", border_style="yellow",
    ))
    while True:
        try:
            ans = console.input("[bold yellow]לשלוח את המייל? [Y/N]: [/bold yellow]").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False                       # אין קלט = לא מאשרים (ברירת מחדל בטוחה)
        if ans in ("y", "yes", "כן", "כ"):
            return True
        if ans in ("n", "no", "לא", "ל"):
            return False
        console.print("נא להקליד Y או N")


def run_task(graph, user_input: str) -> None:
    """מריץ את הגרף על בקשה אחת: Streaming של הנודים, HITL, תשובה ועלות."""
    reset_usage()  # כל משימה מתחילה עם מונה עלויות נקי
    final_answer = None
    answer_printed = False
    email_status = None
    log.info(f"Task started: {user_input[:120]!r}")
    task_start = time.time()

    # thread_id: מזהה ייחודי לכל משימה. ה-checkpointer שומר את ה-state תחתיו,
    # וכך אחרי עצירה (HITL) הגרף יודע מאיפה להמשיך.
    # run_name / tags / metadata: יופיעו ב-LangSmith ויעזרו למצוא את הריצה
    config = {
        "configurable": {"thread_id": str(uuid.uuid4())},
        "run_name": "LogSentinel",
        "tags": ["cli"],
        "metadata": {"model": llm.DEFAULT_MODEL, "user_input": user_input[:200]},
    }
    graph_input = {"user_input": user_input}

    try:
        # לולאה: מריצים את הגרף. אם הוא נעצר ל-HITL, שואלים את המשתמשת וממשיכים
        while True:
            pending_interrupt = None
            node_started = {}

            # שני סוגי אירועים (stream_mode עם רשימה מחזיר זוגות (mode, chunk)):
            #   "tasks"   → נוד מתחיל (יש "input") או מסתיים
            #   "updates" → מה שהנוד עדכן ב-state
            for mode, chunk in graph.stream(graph_input, config, stream_mode=["tasks", "updates"]):
                if mode == "tasks":
                    if "input" in chunk:                       # נוד מתחיל
                        node_started[chunk["name"]] = time.time()
                        console.print(f"[cyan][INFO][/cyan] Entering Node: [bold]{chunk['name']}[/bold]...")
                    continue

                for node_name, update in chunk.items():        # mode == "updates"
                    if node_name == "__interrupt__":           # הגרף עצר ומחכה לנו
                        pending_interrupt = update[0].value
                        continue
                    took = time.time() - node_started.get(node_name, time.time())
                    detail = node_detail(node_name, update)
                    console.print(f"[green]       ✓ {node_name}[/green] [dim]({took:.1f}s)[/dim]"
                                  f"{'  ' + escape(detail) if detail else ''}")
                    # נוד שלא עדכן כלום (כמו save_state) מגיע כ-None, ולכן בודקים קודם שיש update
                    if update and "answer" in update:
                        final_answer = update["answer"]
                    if update and "email_status" in update:
                        email_status = update["email_status"]

            if pending_interrupt is None:
                break  # הגרף סיים

            # --- HITL: קודם מציגים את הדוח, ואז מבקשים אישור ---
            if final_answer and not answer_printed:
                console.print(f"\n[green]🤖 {escape(final_answer)}[/green]\n")
                answer_printed = True
            approved = ask_email_approval(pending_interrupt["email"])
            graph_input = Command(resume=approved)   # ממשיכים את הגרף מאותה נקודה, עם התשובה

    except AgentError as e:
        # שגיאה שאנחנו מכירים: מציגים הסבר ברור
        log.error(f"Task failed: {str(e).splitlines()[0]}")
        console.print(Panel(escape(str(e)), title="❌ שגיאה", border_style="red"))
        print_usage_table()
        return
    except Exception as e:
        # שגיאה לא צפויה: עדיין לא קורסים. מציגים הסבר, והפרטים המלאים נשמרים בלוג
        log.exception(f"Unexpected error: {type(e).__name__}: {e}")
        console.print(Panel(
            f"{type(e).__name__}: {escape(str(e))}\n\n[dim]הפרטים המלאים (Traceback) נשמרו ב: {LOG_FILE}[/dim]",
            title="❌ שגיאה לא צפויה", border_style="red"))
        return

    # escape: טקסט כמו "[error]" מתוך לוג לא יתפרש כפקודת עיצוב של rich
    if not answer_printed:
        console.print(f"\n[green]🤖 {escape(final_answer or '')}[/green]\n")
    if email_status:
        console.print(f"{escape(email_status)}\n")
    print_usage_table()
    console.print(f"[dim]⏱️  זמן כולל: {time.time() - task_start:.1f} שניות[/dim]\n")

    session_history.append({
        "time": datetime.now().strftime("%H:%M:%S"),
        "input": user_input,
        "answer": final_answer or "",
        "email_status": email_status,
        "cost": sum(r["theoretical_cost"] for r in usage_log),
    })
    log.info(f"Task finished. LLM calls={len(usage_log)}")


# ==================================================================
# 3. הלולאה הראשית
# ==================================================================
def handle_command(graph, text: str) -> bool:
    """מחליט לאן הקלט הולך. מחזיר False אם צריך לצאת."""
    cmd, _, arg = text.partition(" ")
    cmd = cmd.lower()

    if cmd in ("exit", "quit", "יציאה"):
        return False
    if cmd in ("help", "?", "עזרה"):
        cmd_help()
    elif cmd == "status":
        cmd_status()
    elif cmd == "tools":
        cmd_tools()
    elif cmd == "model":
        cmd_model(arg.strip())
    elif cmd == "config" and arg.strip().lower() == "email":
        cmd_config_email()
    elif cmd == "summary":
        cmd_summary()
    else:
        run_task(graph, text)      # כל השאר: משימה לאייג'נט (דרך הגרף)
    return True


def print_banner() -> None:
    smith = "🟢 LangSmith" if llm.langsmith_enabled() else "⚪ LangSmith כבוי"
    console.print(Panel(
        f"[bold]🛡️  LogSentinel v{__version__}[/bold]: ניתוח לוגים והתראה על תקלות\n"
        f"[dim]model: {llm.DEFAULT_MODEL}  |  {smith}  |  email: {tools.email_settings()['mode']}[/dim]\n"
        f"הקלידי [bold]help[/bold] לעזרה, או [bold]exit[/bold] ליציאה.",
        border_style="cyan"))


def main() -> None:
    print_banner()
    graph = build_graph()

    # מצב "בקשה אחת": python -m agent.cli --ask "השאלה"
    if len(sys.argv) >= 3 and sys.argv[1] == "--ask":
        try:
            handle_command(graph, " ".join(sys.argv[2:]))
        except AgentError as e:
            console.print(Panel(escape(str(e)), title="❌ שגיאה", border_style="red"))
        return

    # לולאה אינטראקטיבית: קוראים קלט → מבצעים → חוזרים חלילה
    while True:
        try:
            text = console.input("[bold yellow]> [/bold yellow]").strip()
        except (EOFError, KeyboardInterrupt):  # Ctrl+C / Ctrl+Z
            break
        if not text:
            continue
        try:
            if not handle_command(graph, text):
                break
        except AgentError as e:   # שגיאות מפקודות המערכת (למשל model עם שם לא קיים)
            console.print(Panel(escape(str(e)), title="❌ שגיאה", border_style="red"))

    if session_history:
        total = sum(h["cost"] for h in session_history)
        console.print(f"📊 סיכום סשן: {len(session_history)} משימות, עלות תיאורטית ${total:.4f}")
    console.print("להתראות 👋")


if __name__ == "__main__":
    main()
