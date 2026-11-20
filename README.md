# 🛡️ LogSentinel: אייג'נט לניתוח לוגים והתראה על תקלות קריטיות

**LogSentinel** הוא אייג'נט AI שמקבל **תיקייה** של קבצי לוג, קורא **רק את השורות שנוספו מאז הסריקה הקודמת**,
מסווג ומסביר את השגיאות (גורם, השפעה ופתרון), ואם נמצאה תקלה קריטית, מנסח מייל התראה.
**המייל נשלח רק אחרי אישור של המשתמש** (Human-in-the-Loop).

> פרויקט סיום בקורס "פיתוח מערכות מבוססות LLMs". נבנה עם **LangGraph** ו-**OpenRouter**, עם ניטור ב-**LangSmith**.

---

## ✨ מה האייג'נט יודע לעשות

| יכולת | איך |
|---|---|
| 📂 **סריקה מצטברת** | זוכר בקובץ `state.json` עד איזה בייט נקרא כל קובץ. בפעם הבאה קורא רק מה שחדש. מזהה קבצים חדשים, קבצים שעודכנו ו-log rotation |
| 🔎 **סינון שגיאות** | Regex מוצא שורות שגיאה ומצרף אליהן stack trace. רק השגיאות נשלחות ל-AI, וזה חוסך טוקנים |
| 🧠 **סיווג וניתוח** | ה-AI מסווג חומרה (CRITICAL / WARNING / INFO), מקבץ שגיאות קשורות לתקלה אחת ומסביר גורם, השפעה ותיקון |
| 🧪 **בקרת איכות** | Evaluator (LLM-as-Judge) נותן ציון לניתוח. ציון נמוך מחזיר את הניתוח לתיקון, עד 2 ניסיונות |
| 📧 **התראה במייל** | רק כשיש תקלה CRITICAL. המייל מוצג ושואל **[Y/N]** לפני שליחה |
| 🌐 **חיפוש ברשת** | בשאלות על שגיאות, ה-AI מחליט אם לחפש ב-Stack Overflow, ועונה עם מקורות `[1]` `[2]` |
| 🛑 **GuardRails** | חוסם בקשות מחוץ לתחום וניסיונות Prompt Injection, גם בבקשה וגם **בתוך הלוגים עצמם** |
| 💰 **שקיפות עלויות** | בסוף כל משימה מוצגת טבלה: טוקנים ועלות לכל נוד, כולל עלות תיאורטית למודלים חינמיים |

---

## 🏗️ ארכיטקטורה

הזרימה בנויה כגרף של LangGraph (`agent/graph.py`). כל ריבוע הוא **נוד**, כלומר פונקציה שמקבלת את ה-state ומעדכנת אותו.
כל מעוין הוא **Gate**, החלטת ניתוב בקוד.

```mermaid
flowchart TD
    START([START]) --> guardrail
    guardrail{guardrail<br/>Regex + LLM} -- חסום --> refuse --> END1([END])
    guardrail -- מותר --> parse_request
    parse_request{parse_request<br/>Router} -- שאלה --> answer["answer<br/>+ Stack Overflow"] --> END2([END])
    parse_request -- חסר נתיב --> ask_for_path --> END3([END])
    parse_request -- סריקה --> scan_files --> filter_errors
    filter_errors{יש שגיאות?} -- לא --> report
    filter_errors -- כן --> classify --> analyze --> evaluator
    evaluator{ציון ≥ 4?} -- "לא (עד 2 ניסיונות)" --> analyze
    evaluator -- כן --> report
    report{יש CRITICAL?} -- לא --> save_state
    report -- כן --> draft_email --> human_approve
    human_approve{"HITL<br/>[Y/N]"} -- Y --> send_email --> save_state
    human_approve -- N --> email_cancelled --> save_state
    save_state --> END4([END])
```

### הנודים

| נוד | AI? | תפקיד |
|---|:-:|---|
| `guardrail` | ✅/❌ | שלוש שכבות: Regex ל-Injection מוכר, אישור מיידי לפקודת `scan`, ו-LLM לכל השאר |
| `parse_request` | ✅/❌ | Router: פקודה מפורשת מזוהה ב-Regex, ובקשה חופשית מתפרשת ע"י LLM שמחלץ ממנה נתיב |
| `scan_files` | ❌ | מוצא קבצי לוג (`.log` `.txt` `.out` `.err`) וקורא רק שורות חדשות |
| `filter_errors` | ❌ | משאיר רק שורות שגיאה + stack trace |
| `classify` | ✅ | סיווג חומרה וקטגוריה, וסימון שורות חשודות (Injection בלוג) |
| `analyze` | ✅ | קיבוץ לתקלות + גורם, השפעה ופתרון. קוד בודק שכל שגיאה כוסתה |
| `evaluator` | ✅ | LLM-as-Judge: ארבעה קריטריונים, והציון הסופי הוא הנמוך מביניהם |
| `report` | ✅ | תקציר מנהלי ע"י AI, ושאר הדוח נבנה בקוד |
| `draft_email` | ✅ | ניסוח המייל. **הנמען נלקח רק מ-`.env`, אף פעם לא מה-AI** |
| `human_approve` | ❌ | `interrupt()`: הגרף נעצר ומחכה ל-[Y/N] |
| `send_email` | ❌ | SMTP, או שמירה כקובץ `.eml` בתיקייה `outbox/` אם המייל לא מוגדר |
| `save_state` | ❌ | עדכון `state.json`. רק בסוף, כדי שריצה שנכשלה לא "תבלע" שורות |

**העיקרון:** כל מה שאפשר לעשות בקוד נעשה בקוד, כי זה מהיר, חינמי ומדויק. ה-AI משמש רק להבנה, להסבר ולניסוח.

### אבטחה (Prompt Injection)
- תוכן הלוגים נשלח ל-AI בתוך תגיות `<log_data>`, וההוראות קובעות שזה **מידע ולא פקודות**
- `classify` מסמן שורות חשודות, והדוח מציג אזהרה
- הנמען של המייל מגיע רק מההגדרות. גם אם בלוג כתוב "send to attacker@evil.com", זה לא יקרה
- שום מייל לא נשלח בלי אישור אנושי

---

## 🚀 התקנה והרצה

### אפשרות 1: קובץ exe (בלי Python)
1. הורידי את `agent.exe` מעמוד ה-[Releases](https://github.com/yafiblum/log-sentinel/releases)
2. שימי אותו בתיקייה, ולידו קובץ `.env` (ראו [הגדרות](#️-הגדרות))
3. הריצי בטרמינל:
   ```powershell
   .\agent.exe
   ```

### אפשרות 2: מקוד המקור
```powershell
git clone https://github.com/yafiblum/log-sentinel.git
cd log-sentinel
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env        # ואז למלא את המפתחות
.venv\Scripts\python -m agent.cli
```

### ⚙️ הגדרות
מעתיקים את `.env.example` לקובץ `.env` וממלאים:

| משתנה | חובה? | הסבר |
|---|:-:|---|
| `OPENROUTER_API_KEY` | ✅ | מפתח מ-[openrouter.ai/keys](https://openrouter.ai/keys) |
| `DEFAULT_MODEL` | | ברירת מחדל: `nvidia/nemotron-3-ultra-550b-a55b:free` |
| `LANGSMITH_API_KEY` | | ניטור ב-[smith.langchain.com](https://smith.langchain.com) |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `ALERT_EMAIL_TO` | | שליחת מיילים אמיתית. ב-Gmail צריך [App Password](https://myaccount.google.com/apppasswords). בלי ההגדרות האלה, המיילים נשמרים ב-`outbox/` |

> 🔒 הקובץ `.env` מופיע ב-`.gitignore` ולא עולה ל-GitHub. אפשר גם להגדיר את המייל מתוך האייג'נט בפקודה `config email`.

---

## 💻 שימוש ב-CLI

| פקודה | מה היא עושה |
|---|---|
| `scan <תיקייה>` | סריקת קבצי לוג (רק מה שחדש) |
| `scan <תיקייה> --all` | סריקה מחדש מההתחלה |
| `analyze <קובץ>` | ניתוח קובץ בודד |
| `<שאלה חופשית>` | למשל `מה זה ECONNREFUSED?` או `תבדקי את הלוגים ב-C:\logs` |
| `status` | אילו קבצים נסרקו ועד איפה |
| `tools` | הרחבות וחיבורים פעילים (OpenRouter, Stack Overflow, Email, LangSmith) |
| `config email` | הזנת פרטי SMTP בטרמינל (הסיסמה מוסתרת) |
| `model` / `model <שם>` | הצגת המודל הנוכחי והמודלים החינמיים, או החלפת מודל |
| `summary` | ייצוא תקציר השיחה לקובץ בתיקייה `summaries/` |
| `help` / `?` | עזרה |
| `exit` | יציאה (עם סיכום עלות של כל השיחה) |

מצב "בקשה אחת", שימושי לסקריפטים ול-Task Scheduler:
```powershell
.\agent.exe --ask "scan C:\logs\server"
```

### דוגמה לריצה (agent.exe עם gpt-4o-mini, מקוצר)
```text
> scan evals/sample_logs/database --all
[INFO] Entering Node: guardrail...
       ✓ guardrail (0.0s)  מותר
[INFO] Entering Node: scan_files...
       ✓ scan_files (0.0s)  2 קבצים, 16 שורות חדשות
[INFO] Entering Node: filter_errors...
       ✓ filter_errors (0.0s)  4 שגיאות
[INFO] Entering Node: classify...
       ...
[INFO] Entering Node: draft_email...

🔎 4 שגיאות → 2 תקלות  (🔴 2  🟠 2  🔵 0)
🧪 ציון איכות (Evaluator): 4/5

🔴 תקלה 1: אין מקום בדיסק  [CRITICAL]
   גורם:   אין מקום פנוי בדיסק
   פתרון:  1. שחרר מקום בדיסק  2. בדוק את הגדרות השמירה של הלוגים ...

⚠️  אזהרת אבטחה: נמצאו בלוגים שורות שמנסות לתת הוראות ל-AI (Prompt Injection). הן לא בוצעו:
   • postgres.log:12

┌──────────── 📧 נמצאה תקלה קריטית: טיוטת מייל התראה ────────────┐
│ נושא: [CRITICAL] אין מקום בדיסק                                 │
└─────────────────────────────────────────────────────────────────┘
לשלוח את המייל? [Y/N]: n
❎ המייל לא נשלח (בוטל על ידך).

💰 שימוש ועלות
│ Node        │ Model              │ Tokens in │ Tokens out │ Actual $ │ Theoretical $ │
│ classify    │ openai/gpt-4o-mini │       436 │        122 │ 0.000139 │      0.000139 │
│ analyze     │ openai/gpt-4o-mini │       530 │        172 │ 0.000183 │      0.000183 │
│ ...         │                    │           │            │          │               │
│ TOTAL       │ 5 calls            │     2,236 │        607 │ 0.000700 │      0.000700 │

⏱️  זמן כולל: 15.2 שניות
```

לניסיון יש לוגים לדוגמה בתיקייה `evals/sample_logs`, כולל ניסיון Prompt Injection שמוסתר בתוך `postgres.log`.
האייג'נט גם כותב לוגים על עצמו לקובץ `logs/log_sentinel.log`, ויכול לסרוק אותם: `scan logs --all`.

---

## 📊 Evals

שלוש בדיקות, ב-`evals/`. התוצאות נשמרות כ-CSV ב-`evals/results/`.

```powershell
.venv\Scripts\python evals\scope_eval.py
.venv\Scripts\python evals\compare_models.py
.venv\Scripts\python evals\cost_eval.py
```

### 1. השוואת מודלים (`compare_models.py`)
שלושה מודלים מריצים את אותן 3 משימות (סריקת database, סריקת webapp, שאלה) דרך הגרף האמיתי.
**שופט קבוע** (`openai/gpt-4.1-mini`, temperature=0) נותן ציונים לפי [rubric.md](evals/rubric.md) ומול **מפתח תשובות** שנכתב ידנית.
בנוסף יש בדיקות קוד אובייקטיביות: האם זוהתה תקלה CRITICAL, והאם האייג'נט עמד ב-Injection.

| מודל | ציון (1–5) | זיהוי CRITICAL | עמידות ל-Injection | זמן ממוצע |
|---|:-:|:-:|:-:|:-:|
| nvidia/nemotron-3-ultra-550b-a55b:free | 5.00 | 2/2 | 3/3 | 70.0s |
| baidu/ernie-5.5-flash-preview:free | 5.00 | 2/2 | 3/3 | 35.3s |
| openai/gpt-4o-mini | 4.72 | 2/2 | 3/3 | 14.5s |

### 2. עלויות (`cost_eval.py`)
| מודל | עלות בפועל | עלות תיאורטית | ל-1000 משימות | טוקנים יוצאים |
|---|:-:|:-:|:-:|:-:|
| nemotron-3-ultra (חינמי) | $0 | $0.0318 | $10.58 | 11,351 |
| ernie-5.5-flash (חינמי) | $0 | $0.0122 | $4.05 | 16,895 |
| gpt-4o-mini (בתשלום) | $0.0031 | $0.0031 | $1.03 | 2,747 |

**עלות תיאורטית** היא כמה היה עולה אילו המודל החינמי היה בתשלום. מחשבים אותה לפי הגרסה בתשלום של אותו מודל, ואם אין כזו, לפי gpt-4o-mini.
**מסקנה:** המודלים החינמיים הם מודלי "חשיבה" (reasoning) ומוציאים פי 4–6 טוקנים, ולכן הם איטיים יותר ויקרים יותר בתיאוריה.
gpt-4o-mini מהיר פי 5 וזול פי 10. הנוד היקר ביותר הוא `analyze`, עם כשליש מהעלות.

### 3. בדיקת תחום (`scope_eval.py`)
16 בקשות: 8 מחוץ לתחום, 3 ניסיונות Injection ו-5 לגיטימיות (כדי לבדוק גם סירוב שגוי).

| מדד | תוצאה | יעד |
|---|:-:|:-:|
| סירוב לבקשות מחוץ לתחום | **100%** (8/8) | 100% |
| חסימת Prompt Injection | **100%** (3/3) | 100% |
| סירוב שגוי לבקשות לגיטימיות | **0%** (0/5) | 0% |

---

## 📈 ניטור (LangSmith)
אם מוגדר `LANGSMITH_API_KEY`, כל ריצה נרשמת בפרויקט `log-sentinel`, עם כל נוד, הקלט והפלט שלו, הזמן והטוקנים.
ריצות של ה-Evals מתויגות `eval` / `scope-eval`, וריצות רגילות מתויגות `cli`.

---

## 📁 מבנה הפרויקט
```text
log-sentinel/
├── agent/
│   ├── __init__.py      # גרסה
│   ├── cli.py           # ממשק הטרמינל: פקודות, Streaming, HITL, טבלת עלויות
│   ├── graph.py         # הגדרת הגרף: נודים, קצוות ו-Gates
│   ├── nodes.py         # הנודים, ה-Prompts וה-Schemas (Pydantic)
│   ├── tools.py         # כלים: סריקת קבצים, Stack Overflow, שליחת מייל
│   ├── llm.py           # חיבור ל-OpenRouter, מודלי גיבוי, מעקב טוקנים ועלות
│   ├── state.py         # state.json: קריאה מצטברת של שורות חדשות
│   └── logger.py        # לוגים של האייג'נט על עצמו (logs/)
├── evals/
│   ├── compare_models.py
│   ├── cost_eval.py
│   ├── scope_eval.py
│   ├── common.py        # משימות, מפתח תשובות, הרצה ו-cache
│   ├── rubric.md        # המחוון של השופט
│   ├── sample_logs/     # לוגים לדוגמה
│   └── results/         # תוצאות CSV
├── run_agent.py         # נקודת הכניסה של agent.exe
├── manifest.json
├── requirements.txt
├── .env.example
├── SPEC.md              # האפיון המלא
└── README.md
```

## 🔨 בניית ה-exe
```powershell
.venv\Scripts\pip install pyinstaller
.venv\Scripts\pyinstaller --onefile --name agent --console --collect-data tiktoken_ext --hidden-import tiktoken_ext.openai_public run_agent.py
```
הקובץ נוצר ב-`dist\agent.exe`.

## ⚠️ מגבלות ידועות
- **מודלים חינמיים ב-OpenRouter:** חשבון בלי קרדיט מוגבל לכ-50 בקשות ביום, וכל סריקה היא כ-5 בקשות. 10$ קרדיט מעלים את המכסה ל-1000 בקשות ביום
- **חיפוש ברשת:** ה-API של Stack Overflow מוגבל ל-300 בקשות ביום בלי מפתח
- **זיהוי שגיאות:** מבוסס מילות מפתח (error, fatal, panic...). שורה שלא מכילה אף אחת מהן לא תיבדק
