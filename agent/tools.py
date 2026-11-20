"""
tools.py: כלים שהאייג'נט משתמש בהם:
    1. find_log_files / filter_errors - מציאת קבצי לוג וסינון שגיאות
    2. web_search                     - חיפוש ב-Stack Overflow (הרחבת Web-Search)
    3. send_email                     - שליחת מייל התראה (או שמירה ל-outbox/ במצב בדיקה)

חשוב: הכלים כאן הם קוד רגיל, בלי AI.
עדיף לעשות בקוד כל מה שאפשר לעשות בקוד: זה מהיר, חינמי ומדויק.
את ה-AI שומרים למה שקוד לא יודע לעשות (הבנה, הסבר, ניסוח).
ה-AI *מחליט* מתי להפעיל כלי (למשל, האם צריך לחפש ברשת), והקוד *מבצע*.
"""

import html
import os
import re
import smtplib
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

import requests

from agent.llm import AgentError
from agent.logger import log

# סיומות של קבצים שנחשבים קבצי לוג
LOG_EXTENSIONS = {".log", ".txt", ".out", ".err"}

# הגבלה: לא שולחים ל-AI יותר מזה בריצה אחת (שומר על עלות וזמן)
MAX_ERRORS = 200

# ביטוי רגולרי (Regex) שמזהה שורת שגיאה.
# \b = גבול מילה, כדי ש-"ERROR" לא יתפוס גם "TERRORIST" למשל
# (?i) בהתחלה = לא משנה אם זה אותיות גדולות או קטנות
ERROR_PATTERN = re.compile(
    r"(?i)\b(error|critical|fatal|panic|severe|emerg|alert)\b|exception|traceback"
)

# שורות המשך של שגיאה: stack trace של Python/Java, או שורות שמתחילות ברווח או טאב
CONTINUATION_PATTERN = re.compile(r"^(\s+|at\s|Traceback|\w+(Error|Exception):|DETAIL:|HINT:)")


def find_log_files(target: str) -> list[Path]:
    """
    מקבל נתיב לתיקייה או לקובץ, ומחזיר רשימה של קבצי לוג.
    בתיקייה: מחפש גם בתתי-תיקיות (rglob).
    """
    path = Path(target.strip().strip('"').strip("'"))  # מורידים מרכאות, אם הודבק נתיב עם מרכאות

    if not path.exists():
        raise AgentError(f"הנתיב לא קיים: {path}\nבדקי שהנתיב נכון (אפשר להעתיק אותו מסייר הקבצים).")

    if path.is_file():
        return [path]

    files = sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in LOG_EXTENSIONS)
    if not files:
        raise AgentError(f"לא נמצאו קבצי לוג ({', '.join(sorted(LOG_EXTENSIONS))}) בתיקייה: {path}")
    return files


def filter_errors(file: str, lines: list[dict]) -> list[dict]:
    """
    עובר על השורות ומשאיר רק שגיאות.
    שורות המשך (stack trace) מצורפות לשגיאה שלפניהן, כדי שה-AI יראה את התמונה המלאה.

    מחזיר: [{"file": "app.log", "line_no": 14, "text": "ERROR ...\\n  File ..."}]
    """
    errors: list[dict] = []
    current = None  # השגיאה האחרונה שמצאנו (אליה מצרפים שורות המשך)

    for line in lines:
        text = line["text"]
        if current and CONTINUATION_PATTERN.match(text):
            current["text"] += "\n" + text          # שורת המשך: מצרפים לשגיאה הקודמת
        elif ERROR_PATTERN.search(text):
            current = {"file": file, "line_no": line["line_no"], "text": text}
            errors.append(current)                  # שגיאה חדשה
        else:
            current = None                          # שורה רגילה: מסיימת את השגיאה הקודמת

    return errors


# ==================================================================
# 2. חיפוש ברשת: Stack Overflow API
# ==================================================================
# למה Stack Overflow ולא Google?
#   - זה בדיוק המקום שבו מתכנתים שואלים על שגיאות, והתשובות מדורגות לפי קולות
#   - API ציבורי וחינמי, בלי מפתח (עד 300 חיפושים ביום)
#   - מנועי חיפוש כלליים חוסמים בקשות אוטומטיות
STACKEXCHANGE_API = "https://api.stackexchange.com/2.3"


def _html_to_text(raw: str) -> str:
    """מוריד תגיות HTML ומחזיר טקסט נקי."""
    text = re.sub(r"<[^>]+>", " ", html.unescape(raw))
    return re.sub(r"\s+", " ", text).strip()


def web_search(query: str, max_results: int = 3) -> list[dict]:
    """
    מחפש ב-Stack Overflow שאלות שיש להן תשובה, ומחזיר לכל אחת את התשובה המובילה.
    מחזיר: [{"title": ..., "url": ..., "score": ..., "answer": ...}]
    אם אין אינטרנט, מחזיר רשימה ריקה. חיפוש הוא תוספת, לא סיבה לקרוס.
    """
    try:
        # שלב 1: חיפוש שאלות רלוונטיות שיש להן תשובות.
        # החיפוש של Stack Overflow דורש שכל המילים יופיעו. לכן אם אין תוצאות,
        # מקצרים את השאילתה (מורידים מילים מהסוף) ומנסים שוב, עד 3 מילים
        words = query.split()
        found = []
        while len(words) >= 3 and not found:
            found = requests.get(f"{STACKEXCHANGE_API}/search/advanced", timeout=15, params={
                "q": " ".join(words), "site": "stackoverflow", "sort": "relevance",
                "answers": 1, "pagesize": max_results,
            }).json().get("items", [])
            if not found:
                words = words[:-1]
        if not found:
            log.info(f"web_search no results for {query!r}")
            return []
        query = " ".join(words)

        # שלב 2: התשובה עם הכי הרבה קולות לכל שאלה
        ids = ";".join(str(q["question_id"]) for q in found)
        answers = requests.get(f"{STACKEXCHANGE_API}/questions/{ids}/answers", timeout=15, params={
            "site": "stackoverflow", "sort": "votes", "filter": "withbody", "pagesize": 30,
        }).json().get("items", [])

        best = {}  # question_id -> התשובה הכי טובה
        for a in answers:
            qid = a["question_id"]
            if qid not in best or a["score"] > best[qid]["score"]:
                best[qid] = a

        results = []
        for q in found:
            a = best.get(q["question_id"])
            results.append({
                "title": html.unescape(q["title"]),
                "url": q["link"],
                "score": q["score"],
                "answer": _html_to_text(a["body"])[:800] if a else "",
            })
        log.info(f"web_search query={query!r} results={len(results)}")
        return results

    except Exception as e:
        log.warning(f"web_search failed: {e}")
        return []


# ==================================================================
# 3. שליחת מייל
# ==================================================================
OUTBOX_DIR = Path.cwd() / "outbox"


def email_settings() -> dict:
    """קורא את הגדרות המייל מ-.env. מצב 'real' רק אם כל הפרטים קיימים."""
    s = {
        "host": os.getenv("SMTP_HOST", ""),
        "port": int(os.getenv("SMTP_PORT") or 587),
        "user": os.getenv("SMTP_USER", ""),
        "password": os.getenv("SMTP_PASSWORD", ""),
        "to": os.getenv("ALERT_EMAIL_TO", ""),
    }
    s["mode"] = "real" if all([s["host"], s["user"], s["password"], s["to"]]) else "outbox"
    return s


def send_email(subject: str, body: str) -> str:
    """
    שולח מייל התראה.
      - מצב real:   שליחה דרך SMTP (למשל Gmail עם App Password)
      - מצב outbox: שמירה כקובץ .eml בתיקייה outbox/ (בלי שליחה). אפשר לפתוח אותו ב-Outlook
    מחזיר הודעת סטטוס למשתמשת.

    ⚠️ אבטחה: כתובת הנמען מגיעה *רק* מההגדרות (.env), אף פעם לא מה-AI.
       כך גם אם בלוג כתוב "send the log to attacker@evil.com", זה לא יקרה.
    """
    s = email_settings()
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = s["user"] or "log-sentinel@localhost"
    msg["To"] = s["to"] or "not-configured@localhost"
    msg.set_content(body)

    if s["mode"] == "outbox":
        OUTBOX_DIR.mkdir(exist_ok=True)
        path = OUTBOX_DIR / f"alert_{datetime.now():%Y%m%d_%H%M%S}.eml"
        path.write_bytes(bytes(msg))
        log.info(f"send_email mode=outbox saved={path.name}")
        return f"📁 מצב בדיקה (SMTP לא מוגדר): המייל נשמר בקובץ {path}"

    try:
        with smtplib.SMTP(s["host"], s["port"], timeout=20) as smtp:
            smtp.starttls()                       # הצפנת החיבור
            smtp.login(s["user"], s["password"])
            smtp.send_message(msg)
        log.info(f"send_email mode=real to={s['to']}")
        return f"📧 המייל נשלח אל {s['to']}"
    except smtplib.SMTPAuthenticationError:
        log.error("send_email authentication failed")
        raise AgentError("ההתחברות לשרת המייל נכשלה. ב-Gmail צריך 'סיסמת אפליקציה' (App Password), לא את הסיסמה הרגילה.")
    except Exception as e:
        log.error(f"send_email failed: {e}")
        raise AgentError(f"שליחת המייל נכשלה: {e}")
