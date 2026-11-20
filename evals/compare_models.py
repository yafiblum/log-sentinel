"""
compare_models.py: Eval #1, השוואת איכות בין מודלים בעזרת LLM-as-Judge.

מה קורה כאן?
    1. כל מודל מריץ את אותן משימות (סריקת database, סריקת webapp, שאלה) דרך הגרף האמיתי
    2. מודל שופט *קבוע* מקבל את התשובה + מפתח תשובות + המחוון (rubric.md) ונותן ציונים 1-5
    3. בנוסף, בדיקות קוד אובייקטיביות: זוהה CRITICAL? האייג'נט עמד ב-Prompt Injection?
    4. תוצאות:  evals/results/compare_models.csv          (שורה לכל מודל × משימה)
                evals/results/compare_models_summary.csv  (שורה לכל מודל: ממוצעים)

הרצה (מתיקיית הפרויקט):
    python evals/compare_models.py
    python evals/compare_models.py --models openai/gpt-4o-mini google/gemma-4-31b-it:free
    python evals/compare_models.py --tasks db_scan question
    python evals/compare_models.py --fresh        # להתעלם מה-cache ולהריץ הכל מחדש

למה שופט קבוע?
    אם כל מודל היה נשפט ע"י שופט אחר, לא היה אפשר להשוות. שופט אחד, מחוון אחד, temperature=0:
    ההבדל היחיד בין השורות הוא המודל הנבדק.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from evals.common import (  # noqa: E402
    DEFAULT_MODELS, RESULTS_DIR, ROOT, TASKS, code_checks, console, load_cache, run_task_cached, save_cache, write_csv,
)

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402
from rich.table import Table  # noqa: E402

from agent import llm  # noqa: E402
from agent.llm import AgentError, call_llm_json  # noqa: E402

# מודל השופט: קבוע לכל ההשוואה. בתשלום (זול מאוד), ולא אחד מהמודלים הנבדקים
JUDGE_MODEL = "openai/gpt-4.1-mini"
CRITERIA = ["correctness", "completeness", "severity", "actionability", "safety", "clarity"]


class JudgeResult(BaseModel):
    """ה-JSON שהשופט חייב להחזיר (Pydantic בודק שכל ציון בין 1 ל-5)."""
    correctness: int = Field(ge=1, le=5)
    completeness: int = Field(ge=1, le=5)
    severity: int = Field(ge=1, le=5)
    actionability: int = Field(ge=1, le=5)
    safety: int = Field(ge=1, le=5)
    clarity: int = Field(ge=1, le=5)
    justification: str


def load_rubric() -> str:
    """קורא את המחוון מ-rubric.md: רק החלק באנגלית שמיועד לשופט."""
    text = (ROOT / "evals" / "rubric.md").read_text(encoding="utf-8")
    start = text.index("## The Rubric")
    end = text.index("## איך מחושב")
    return text[start:end].strip().rstrip("-").strip()


def judge(task_id: str, result: dict, judge_model: str, fresh: bool) -> dict:
    """שולח לשופט את המשימה, מפתח התשובות ותשובת האייג'נט. גם השיפוט נשמר ב-cache."""
    cache = load_cache()
    key = f"judge::{judge_model}::{result['model']}::{task_id}"
    if key in cache and not fresh:
        return cache[key]

    output = result["answer"]
    if result["email_subject"]:
        output += f"\n\n--- Alert email draft ---\nSubject: {result['email_subject']}\n{result['email_body']}"

    messages = [
        SystemMessage(content=load_rubric()),
        HumanMessage(content=(
            f"<task>\n{TASKS[task_id]['input']}\n</task>\n\n"
            f"<expected>\n{TASKS[task_id]['expected']}\n</expected>\n\n"
            f"<agent_output>\n{output}\n</agent_output>")),
    ]
    llm.reset_usage()
    verdict: JudgeResult = call_llm_json("judge", messages, JudgeResult, model=judge_model)
    scores = verdict.model_dump()
    scores["overall"] = round(sum(scores[c] for c in CRITERIA) / len(CRITERIA), 2)
    scores["judge_cost"] = sum(r["actual_cost"] for r in llm.usage_log)

    cache = load_cache()
    cache[key] = scores
    save_cache(cache)
    return scores


def main() -> None:
    parser = argparse.ArgumentParser(description="Eval #1: model comparison with LLM-as-Judge")
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    parser.add_argument("--tasks", nargs="+", default=list(TASKS), choices=list(TASKS))
    parser.add_argument("--judge", default=JUDGE_MODEL, help="judge model (keep it fixed between runs!)")
    parser.add_argument("--fresh", action="store_true", help="ignore the cache and run everything again")
    args = parser.parse_args()

    console.rule("[bold]Eval #1: השוואת מודלים (LLM-as-Judge)")
    console.print(f"מודלים: {', '.join(args.models)}\nמשימות: {', '.join(args.tasks)}\nשופט: [bold]{args.judge}[/bold]\n")

    rows = []
    for model in args.models:
        console.print(f"[bold cyan]{model}[/bold cyan]")
        for task_id in args.tasks:
            result = run_task_cached(model, task_id, args.fresh)
            row = {"model": model, "task": task_id, "status": result["status"]}

            if result["status"] == "ok":
                try:
                    scores = judge(task_id, result, args.judge, args.fresh)
                    row.update({c: scores[c] for c in CRITERIA})
                    row["overall"] = scores["overall"]
                except AgentError as e:
                    row["status"] = "judge_error"
                    scores = {"justification": "", "judge_cost": 0}
                    console.print(f"    [red]השופט נכשל: {str(e).splitlines()[0]}[/red]")
                row.update(code_checks(task_id, result))
                row.update({
                    "internal_eval_score": result["eval_score"] if result["eval_score"] is not None else "",
                    "errors_found": result["n_errors"],
                    "incidents": result["n_incidents"],
                    "latency_s": result["latency_s"],
                    "llm_calls": result["calls"],
                    "judge_justification": scores["justification"],
                    "error": "",
                })
            else:
                row["error"] = result["error"]
            rows.append(row)

    write_csv(RESULTS_DIR / "compare_models.csv", rows)

    # --- סיכום לכל מודל ---
    summary = []
    for model in args.models:
        ok = [r for r in rows if r["model"] == model and "overall" in r]
        if not ok:
            summary.append({"model": model, "tasks_scored": 0})
            continue
        avg = lambda k: round(sum(r[k] for r in ok) / len(ok), 2)  # noqa: E731
        checks = [r for r in ok if r["critical_ok"] != ""]
        summary.append({
            "model": model,
            "tasks_scored": len(ok),
            "overall": avg("overall"),
            **{c: avg(c) for c in CRITERIA},
            "critical_detection_rate": f"{sum(r['critical_ok'] for r in checks)}/{len(checks)}" if checks else "",
            "injection_resisted": f"{sum(r['injection_ok'] for r in ok)}/{len(ok)}",
            "avg_latency_s": avg("latency_s"),
            "judge_model": args.judge,
        })
    write_csv(RESULTS_DIR / "compare_models_summary.csv", summary)

    table = Table(title="תוצאות (ממוצע 1-5)", show_lines=False)
    for col in ["model", "overall", *CRITERIA, "CRITICAL", "injection", "latency"]:
        table.add_column(col, justify="right" if col != "model" else "left")
    for s in sorted(summary, key=lambda s: s.get("overall", 0), reverse=True):
        if not s["tasks_scored"]:
            table.add_row(s["model"], "[red]נכשל[/red]", *[""] * (len(CRITERIA) + 3))
            continue
        table.add_row(s["model"], f"[bold]{s['overall']}[/bold]", *[str(s[c]) for c in CRITERIA],
                      s["critical_detection_rate"], s["injection_resisted"], f"{s['avg_latency_s']}s")
    console.print(table)

    scored = [s for s in summary if s["tasks_scored"]]
    if scored:
        best = max(scored, key=lambda s: s["overall"])
        console.print(f"🏆 המודל המוביל: [bold]{best['model']}[/bold] (ציון {best['overall']})")
    if any(r["status"] != "ok" for r in rows):
        console.print("[yellow]⚠️  חלק מהריצות נכשלו. הריצי שוב: מה שהצליח נשמר ב-cache ולא ירוץ פעמיים.[/yellow]")


if __name__ == "__main__":
    main()
