"""
llm.py: חיבור ל-OpenRouter ומעקב אחרי טוקנים ועלויות.

שלושה דברים קורים כאן:
1. טעינת מפתחות מקובץ .env
2. יצירת "לקוח" LLM שמדבר עם OpenRouter
3. רישום של כל קריאה ל-LLM: כמה טוקנים נצרכו וכמה זה עלה

למה ChatOpenAI אם אנחנו לא משתמשים ב-OpenAI?
    OpenRouter בנה את ה-API שלו בדיוק כמו של OpenAI. לכן אפשר להשתמש
    בלקוח של OpenAI ופשוט לשנות לו את הכתובת (base_url) ל-OpenRouter.
"""

import os
from pathlib import Path

import requests
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ValidationError

from agent.logger import log

# ------------------------------------------------------------------
# 1. טעינת הגדרות
# ------------------------------------------------------------------
# מחפשים .env בתיקייה שממנה מריצים את התוכנה (חשוב גם בשביל ה-exe בהמשך)
load_dotenv(Path.cwd() / ".env")

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL") or "nvidia/nemotron-3-ultra-550b-a55b:free"

# מודלי גיבוי: אם המודל הראשי עמוס (שגיאה 429, נפוץ במודלים חינמיים), עוברים לבא בתור
FALLBACK_MODELS = [
    "nvidia/nemotron-3.5-super-vl:free",
    "google/gemma-4-31b-it:free",
    "inclusionai/ling-3.2-flash:free",
]

# ב-Evals מכבים את הגיבוי: כשמשווים מודלים, כל תשובה חייבת להגיע מהמודל שנבדק
ALLOW_FALLBACK = True

# מודל "ייחוס" לחישוב עלות תיאורטית, אם למודל חינמי אין גרסה בתשלום
REFERENCE_PAID_MODEL = "openai/gpt-4o-mini"

# אם אין מפתח LangSmith, מכבים את הניטור כדי שלא יופיעו שגיאות
if not os.getenv("LANGSMITH_API_KEY"):
    os.environ["LANGSMITH_TRACING"] = "false"


class AgentError(Exception):
    """שגיאה "ידידותית": ההודעה שלה מוצגת למשתמשת כמו שהיא, בלי traceback מפחיד."""


# ------------------------------------------------------------------
# 2. יצירת הלקוח
# ------------------------------------------------------------------
def get_llm(model: str | None = None, temperature: float = 0.0) -> ChatOpenAI:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        log.error("OPENROUTER_API_KEY is missing from .env")
        raise AgentError(
            "חסר מפתח OpenRouter.\n"
            "  1. צרי מפתח ב- https://openrouter.ai/keys\n"
            "  2. העתיקי את .env.example לקובץ בשם .env\n"
            "  3. הדביקי את המפתח אחרי OPENROUTER_API_KEY="
        )
    return ChatOpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=api_key,
        model=model or DEFAULT_MODEL,
        temperature=temperature,  # 0 = תשובות עקביות וצפויות (מתאים לניתוח, לא ליצירתיות)
        # תקרת אורך לתשובה. בלי תקרה, OpenRouter "שומר" מראש את המקסימום של המודל (למשל 65K טוקנים)
        # ודורש שיהיה מספיק קרדיט לכל הסכום (שגיאה 402). תקרה גם מגבילה עלות של תשובה "שהשתוללה"
        max_tokens=8192,
        timeout=90,
        max_retries=2,
    )


# ------------------------------------------------------------------
# 3. תמחור: כמה עולה טוקן בכל מודל?
# ------------------------------------------------------------------
_prices: dict[str, tuple[float, float]] = {}  # model_id -> (מחיר לטוקן קלט, מחיר לטוקן פלט)


def _load_prices() -> None:
    """מושך את רשימת המחירים של OpenRouter (פעם אחת לכל ריצה). הרשימה ציבורית ולא דורשת מפתח."""
    if _prices:
        return
    try:
        data = requests.get(f"{OPENROUTER_BASE_URL}/models", timeout=20).json()["data"]
        for m in data:
            p = m.get("pricing", {})
            _prices[m["id"]] = (float(p.get("prompt", 0)), float(p.get("completion", 0)))
    except Exception:
        pass  # אם אין אינטרנט למחירים, נציג עלות 0. זה לא סיבה לקרוס


def get_price(model: str) -> tuple[float, float]:
    _load_prices()
    return _prices.get(model, (0.0, 0.0))


def get_theoretical_price(model: str) -> tuple[float, float]:
    """
    עבור מודל חינמי (שמו מסתיים ב-:free): כמה *היה* עולה.
    קודם מנסים את הגרסה בתשלום של אותו מודל, ואם אין כזו, את מודל הייחוס.
    """
    if not model.endswith(":free"):
        return get_price(model)
    paid_twin = get_price(model.removesuffix(":free"))
    return paid_twin if paid_twin != (0.0, 0.0) else get_price(REFERENCE_PAID_MODEL)


def list_free_models() -> list[str]:
    """כל המודלים החינמיים שזמינים כרגע ב-OpenRouter."""
    _load_prices()
    return sorted(m for m in _prices if m.endswith(":free"))


def set_model(name: str) -> None:
    """מחליף את המודל הפעיל (פקודת model ב-CLI). בודק קודם שהמודל קיים."""
    global DEFAULT_MODEL
    _load_prices()
    if _prices and name not in _prices:
        raise AgentError(f"המודל '{name}' לא קיים ב-OpenRouter. הקלידי model כדי לראות רשימה.")
    DEFAULT_MODEL = name
    log.info(f"model changed to {name}")


def langsmith_enabled() -> bool:
    return os.getenv("LANGSMITH_TRACING", "").lower() == "true" and bool(os.getenv("LANGSMITH_API_KEY"))


# ------------------------------------------------------------------
# 4. מעקב שימוש: כל קריאה ל-LLM נרשמת כאן
# ------------------------------------------------------------------
usage_log: list[dict] = []


def reset_usage() -> None:
    usage_log.clear()


def call_llm(node: str, messages: list, model: str | None = None, **kwargs):
    """
    הפונקציה שכל הנודים משתמשים בה כדי לדבר עם ה-LLM.
    היא מבצעת את הקריאה, ובנוסף רושמת טוקנים ועלות תחת שם הנוד.
    """
    # רשימת המודלים לניסיון: קודם המבוקש, ואז הגיבויים (בלי כפילויות)
    model = model or DEFAULT_MODEL
    candidates = [model] + ([m for m in FALLBACK_MODELS if m != model] if ALLOW_FALLBACK else [])

    for i, model in enumerate(candidates):
        llm = get_llm(model, **kwargs)
        try:
            response = llm.invoke(messages)
            break  # הצליח, יוצאים מהלולאה
        except Exception as e:
            is_last = i == len(candidates) - 1
            # מגבלה יומית משותפת לכל המודלים החינמיים: אין טעם לנסות מודל גיבוי חינמי אחר
            daily_limit = "per-day" in str(e)
            if "429" in str(e) and not is_last and not daily_limit:
                log.warning(f"Model {model} rate-limited (429), falling back to {candidates[i + 1]}")
                continue  # המודל עמוס, מנסים את הבא
            log.error(f"LLM call failed in node={node} model={model}: {str(e)[:300]}")
            raise AgentError(_explain_api_error(e, model)) from e

    # usage_metadata: LangChain מחזיר כמה טוקנים נכנסו (input) ויצאו (output)
    u = response.usage_metadata or {}
    tin, tout = u.get("input_tokens", 0), u.get("output_tokens", 0)
    pin, pout = get_price(model)
    tpin, tpout = get_theoretical_price(model)

    log.info(f"LLM call node={node} model={model} tokens_in={tin} tokens_out={tout}")
    usage_log.append({
        "node": node,
        "model": model,
        "input_tokens": tin,
        "output_tokens": tout,
        "actual_cost": tin * pin + tout * pout,            # מה שבאמת שולם
        "theoretical_cost": tin * tpin + tout * tpout,     # מה שהיה עולה במודל בתשלום
    })
    return response


# ------------------------------------------------------------------
# 5. קריאה שמחזירה JSON מובנה (Structured Output)
# ------------------------------------------------------------------
def call_llm_json(node: str, messages: list, schema: type[BaseModel], model: str | None = None, retries: int = 1):
    """
    כמו call_llm, אבל מחייבת את ה-AI להחזיר JSON במבנה מוגדר (schema של Pydantic).

    למה זה חשוב?
        טקסט חופשי טוב לבני אדם, אבל הקוד צריך נתונים מסודרים. למשל, כדי לדעת
        אם יש תקלה CRITICAL ולהחליט אם לשלוח מייל.

    איך זה עובד?
        1. שולחים את הבקשה (ה-prompt כבר מבקש JSON)
        2. מחלצים את ה-JSON מהתשובה ובודקים שהוא תואם ל-schema
        3. אם לא: מחזירים ל-AI את השגיאה ומבקשים לתקן (ניסיון נוסף)
    """
    for attempt in range(retries + 1):
        response = call_llm(node, messages, model)
        try:
            return schema.model_validate_json(_extract_json(response.content))
        except (ValidationError, ValueError) as e:
            log.warning(f"node={node} invalid JSON (attempt {attempt + 1}): {str(e)[:200]}")
            # מוסיפים לשיחה את התשובה השגויה ואת בקשת התיקון
            messages = messages + [
                AIMessage(content=response.content),
                HumanMessage(content=f"Your answer was not valid JSON for the required schema. Error: {str(e)[:500]}\n"
                                     f"Return ONLY the corrected JSON, nothing else."),
            ]
    raise AgentError(f"ה-AI החזיר תשובה בפורמט לא תקין בנוד {node}. נסי שוב או החליפי מודל.")


def _extract_json(text: str) -> str:
    """מחלץ את ה-JSON מתוך התשובה. מודלים לפעמים עוטפים אותו ב-```json ... ``` או מוסיפים הסבר."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object found in the response")
    return text[start: end + 1]


def _explain_api_error(e: Exception, model: str) -> str:
    """מתרגם שגיאות טכניות מה-API להודעה ברורה."""
    msg = str(e)
    if "401" in msg:
        return "המפתח של OpenRouter לא תקין (401). בדקי את OPENROUTER_API_KEY בקובץ .env"
    if "402" in msg:
        return "אין מספיק קרדיט בחשבון OpenRouter (402). בחרי מודל חינמי (:free) או הוסיפי קרדיט"
    if "404" in msg:
        return f"המודל '{model}' לא נמצא ב-OpenRouter (404). בדקי את השם ב- https://openrouter.ai/models"
    if "429" in msg and "per-day" in msg:
        return ("נגמרה המכסה היומית של מודלים חינמיים ב-OpenRouter (429).\n"
                "  • המכסה מתאפסת פעם ביום (בחצות UTC, כלומר 02:00 בלילה שעון ישראל)\n"
                "  • או: הוספת 10$ קרדיט מגדילה את המכסה ל-1000 בקשות ביום: https://openrouter.ai/settings/credits\n"
                "  • או: מעבר למודל בתשלום זול, למשל:  model openai/gpt-4o-mini")
    if "429" in msg:
        return "יותר מדי בקשות (429). במודלים חינמיים יש מגבלת קצב. חכי דקה ונסי שוב, או החליפי מודל"
    if "timed out" in msg.lower() or "connection" in msg.lower():
        return "אין חיבור ל-OpenRouter. בדקי את החיבור לאינטרנט"
    return f"שגיאה מה-LLM: {msg[:300]}"
