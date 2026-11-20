"""
state.py: "הזיכרון" של מה שכבר נקרא (קובץ state.json).

הרעיון:
    לכל קובץ לוג שומרים עד איזה בייט קראנו (offset).
    בריצה הבאה קופצים ישר לשם וקוראים רק את מה שנוסף אחרי כן.

    דוגמה ל-state.json:
    {
      "C:/logs/app.log": {"offset": 48213, "size": 48213, "mtime": 1763561234.5, "lines": 812}
    }

    offset = עד איזה בייט קראנו
    size   = גודל הקובץ בזמן הקריאה האחרונה
    mtime  = זמן השינוי האחרון של הקובץ
    lines  = כמה שורות קראנו עד עכשיו (כדי להציג מספרי שורות נכונים)

הערה: זה *לא* ה-State של LangGraph (AgentState). זה קובץ על הדיסק,
שנשמר בין ריצות שונות של התוכנה.
"""

import json
from pathlib import Path

STATE_FILE = Path.cwd() / "state.json"


def load_state() -> dict:
    """טוען את state.json. אם הקובץ לא קיים או פגום, מתחילים מאפס."""
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(updates: dict) -> None:
    """ממזג עדכונים חדשים לתוך state.json ושומר."""
    state = load_state()
    state.update(updates)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def read_new_lines(path: Path, state: dict, read_all: bool = False) -> tuple[list[dict], dict | None, str]:
    """
    קורא מקובץ רק את השורות שעוד לא נקראו.

    מחזיר שלושה דברים:
        lines  - רשימת שורות חדשות: [{"line_no": 13, "text": "..."}]
        entry  - הרשומה המעודכנת לשמירה ב-state.json (או None אם לא השתנה כלום)
        status - "new" / "updated" / "rotated" / "unchanged" (בשביל ההדפסה למשתמשת)
    """
    key = path.resolve().as_posix()  # מפתח אחיד, למשל "C:/logs/app.log"
    stat = path.stat()
    prev = None if read_all else state.get(key)

    # --- מחליטים מאיפה להתחיל לקרוא ---
    if prev is None:
        start, start_line, status = 0, 0, "new"                 # קובץ שלא ראינו מעולם
    elif stat.st_size < prev["offset"]:
        start, start_line, status = 0, 0, "rotated"             # הקובץ קטן: נמחק ונוצר מחדש (log rotation)
    elif stat.st_size == prev["offset"]:
        return [], None, "unchanged"                            # אין שום דבר חדש
    else:
        start, start_line, status = prev["offset"], prev.get("lines", 0), "updated"

    # --- קוראים מה-offset ועד הסוף ---
    # קוראים בבינארי (rb) כי offset נמדד בבייטים, לא בתווים
    with open(path, "rb") as f:
        f.seek(start)
        chunk = f.read()

    # אם השורה האחרונה לא נגמרת ב-"\n", ייתכן שהתוכנה עדיין כותבת אותה.
    # במקרה כזה לא קוראים אותה עכשיו; היא תיקרא בפעם הבאה, כשתהיה שלמה
    last_newline = chunk.rfind(b"\n")
    if last_newline == -1:
        return [], None, "unchanged"
    chunk = chunk[: last_newline + 1]

    # errors="replace": אם יש תו לא חוקי, מחליפים אותו ולא קורסים
    text_lines = chunk.decode("utf-8", errors="replace").splitlines()
    lines = [
        {"line_no": start_line + i + 1, "text": t}
        for i, t in enumerate(text_lines)
    ]

    entry = {
        "offset": start + len(chunk),
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "lines": start_line + len(text_lines),
    }
    return lines, entry, status
