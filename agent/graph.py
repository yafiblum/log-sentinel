"""
graph.py: הגדרת ה-Workflow, כלומר איך הנודים מחוברים זה לזה.

הגרף בשלב 5:

               START
                 │
            [guardrail]            ← Regex + LLM: בתחום? ניסיון Injection?
             /        \\
      חסום           מותר
        │              │
    [refuse]     [parse_request]   ← Regex או LLM: סריקה / שאלה / חסר נתיב
        │        /     │      \\
        │    שאלה  חסר נתיב  סריקה
        │      │       │        │
        │  [answer] [ask_for_path] [scan_files]
        │      │       │        │
        │      │       │  [filter_errors]
        │      │       │     /       \\
        │      │       │ יש שגיאות   אין
        │      │       │    │          │
        │      │       │ [classify]    │
        │      │       │    │          │
        │      │       │ [analyze] ◄───────┐
        │      │       │    │          │   │ ציון < 4 (עד 2 ניסיונות)
        │      │       │ [evaluator] ──────┘
        │      │       │    │ ציון ≥ 4
        │      │       │ [report] ◄────┘
        │      │       │    │
        │      │       │  יש CRITICAL? (Gate)
        │      │       │   לא │      │ כן
        │      │       │      │  [draft_email]        ← LLM: ניסוח מייל
        │      │       │      │      │
        │      │       │      │  [human_approve]      ← HITL: עצירה ו-[Y/N]
        │      │       │      │    Y │      │ N
        │      │       │      │ [send_email] [email_cancelled]
        │      │       │      │      │      │
        │      │       │  [save_state] ◄────┘
         \\     |       |    /
                   END

(שלב 6: answer מחפש גם ב-Stack Overflow כשצריך)
"""

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, START, END

from agent import nodes
from agent.nodes import AgentState


# ------------------------------------------------------------------
# פונקציות ניתוב (Conditional Edges): מחזירות את *שם* הנוד הבא
# ------------------------------------------------------------------
def route_after_guardrail(state: AgentState) -> str:
    """Gate: בקשה מותרת ממשיכה, בקשה חסומה מקבלת סירוב."""
    return "parse_request" if state["in_scope"] else "refuse"


def route_by_intent(state: AgentState) -> str:
    """Router: סריקה, שאלה, או חסר נתיב?"""
    return {"scan": "scan_files", "question": "answer", "need_path": "ask_for_path"}[state["intent"]]


def route_after_filter(state: AgentState) -> str:
    """Gate: אם אין שגיאות, אין מה לסווג. דילוג ישר לדוח."""
    return "classify" if state["errors"] else "report"


def route_after_evaluator(state: AgentState) -> str:
    """
    Gate של בקרת איכות:
      - ציון טוב מספיק                       → ממשיכים לדוח
      - ציון נמוך, ועוד לא מיצינו את הניסיונות → חוזרים ל-analyze (לולאה!)
      - ציון נמוך, אבל כבר ניסינו מספיק      → ממשיכים לדוח (עם הציון הנמוך, בשקיפות)
    הגבלת מספר הניסיונות מונעת לולאה אינסופית (ועלות אינסופית).
    """
    if state["eval_score"] >= nodes.EVAL_PASS_SCORE:
        return "report"
    if state["analyze_attempts"] < nodes.MAX_ANALYZE_ATTEMPTS:
        return "analyze"
    return "report"


def route_after_report(state: AgentState) -> str:
    """Gate: מייל רק אם יש תקלה קריטית."""
    return "draft_email" if state.get("has_critical") else "save_state"


def route_after_approval(state: AgentState) -> str:
    """HITL Gate: שולחים רק אם המשתמשת אישרה."""
    return "send_email" if state["approved"] else "email_cancelled"


def build_graph():
    builder = StateGraph(AgentState)

    # --- נודים ---
    builder.add_node("guardrail", nodes.guardrail)
    builder.add_node("refuse", nodes.refuse)
    builder.add_node("parse_request", nodes.parse_request)
    builder.add_node("ask_for_path", nodes.ask_for_path)
    builder.add_node("answer", nodes.answer)
    builder.add_node("scan_files", nodes.scan_files)
    builder.add_node("filter_errors", nodes.filter_errors)
    builder.add_node("classify", nodes.classify)
    builder.add_node("analyze", nodes.analyze)
    builder.add_node("evaluator", nodes.evaluator)
    builder.add_node("report", nodes.report)
    builder.add_node("draft_email", nodes.draft_email)
    builder.add_node("human_approve", nodes.human_approve)
    builder.add_node("send_email", nodes.send_email)
    builder.add_node("email_cancelled", nodes.email_cancelled)
    builder.add_node("save_state", nodes.save_state)

    # --- קצוות ---
    builder.add_edge(START, "guardrail")
    builder.add_conditional_edges("guardrail", route_after_guardrail, ["parse_request", "refuse"])
    builder.add_conditional_edges("parse_request", route_by_intent, ["scan_files", "answer", "ask_for_path"])

    # מסלול הסריקה
    builder.add_edge("scan_files", "filter_errors")
    builder.add_conditional_edges("filter_errors", route_after_filter, ["classify", "report"])
    builder.add_edge("classify", "analyze")
    builder.add_edge("analyze", "evaluator")
    builder.add_conditional_edges("evaluator", route_after_evaluator, ["analyze", "report"])
    builder.add_conditional_edges("report", route_after_report, ["draft_email", "save_state"])

    # מסלול המייל (HITL)
    builder.add_edge("draft_email", "human_approve")
    builder.add_conditional_edges("human_approve", route_after_approval, ["send_email", "email_cancelled"])
    builder.add_edge("send_email", "save_state")
    builder.add_edge("email_cancelled", "save_state")

    # סופי מסלולים
    builder.add_edge("save_state", END)
    builder.add_edge("answer", END)
    builder.add_edge("ask_for_path", END)
    builder.add_edge("refuse", END)

    # checkpointer: שומר את ה-state אחרי כל נוד.
    # בלעדיו interrupt() לא יכול לעבוד, כי הגרף צריך "לזכור" איפה עצר כדי להמשיך משם.
    # InMemorySaver שומר בזיכרון בלבד. מספיק לנו, כי אין צורך בזיכרון בין הפעלות של התוכנה
    return builder.compile(checkpointer=InMemorySaver())
