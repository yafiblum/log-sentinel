"""
logger.py: האייג'נט כותב לוג על עצמו, לקובץ logs/log_sentinel.log

למה?
    1. כך עובדות מערכות אמיתיות: כשמשהו נשבר, הלוג מספר מה קרה.
    2. "אוכלים את האוכל של עצמנו": אפשר לבקש מהאייג'נט  scan logs
       והוא ינתח את הלוגים של עצמו (שגיאות 429, מפתח חסר, נתיב שגוי...).

משתמשים בספריית logging המובנית של Python.
דוגמה לשורה בקובץ:
    2026-11-19 21:40:12,551 INFO  [node] Entering node: guardrail
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path.cwd() / "logs"
LOG_FILE = LOG_DIR / "log_sentinel.log"


def get_logger() -> logging.Logger:
    logger = logging.getLogger("log_sentinel")
    if logger.handlers:          # כבר הוגדר (קוראים לפונקציה מכמה קבצים), לא מגדירים פעמיים
        return logger

    LOG_DIR.mkdir(exist_ok=True)

    # RotatingFileHandler: כשהקובץ מגיע ל-1MB, הוא עובר לקובץ log_sentinel.log.1
    # ונפתח קובץ חדש. כך הלוג לא גדל בלי סוף (זה בדיוק ה-"log rotation" ש-state.py מטפל בו!)
    handler = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s [%(module)s] %(message)s"))

    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False     # לא להדפיס גם לטרמינל. הלוג הולך רק לקובץ
    return logger


log = get_logger()
