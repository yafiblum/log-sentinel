"""
cost_eval.py: Eval #2, כמה עולה כל מודל (טוקנים, דולרים, זמן).

מה נמדד?
    לכל מודל × משימה: מספר קריאות ל-LLM, טוקנים נכנסים/יוצאים, עלות בפועל, עלות תיאורטית וזמן.
      - עלות בפועל    = מה ששולם באמת (במודל חינמי: $0)
      - עלות תיאורטית = כמה *היה* עולה אילו המודל היה בתשלום
                        (הגרסה בתשלום של אותו מודל, ואם אין כזו: מחיר gpt-4o-mini כייחוס)
    הדרישה בפרויקט: לפחות מודל אחד בתשלום, ועלות תיאורטית למודלים החינמיים.

תוצאות:
    evals/results/cost_eval.csv           שורה לכל מודל × משימה
    evals/results/cost_eval_by_node.csv   איפה הכסף הולך: עלות לפי נוד (classify / analyze / ...)
    evals/results/cost_eval_summary.csv   שורה לכל מודל + תחזית ל-1000 סריקות

הרצה:
    python evals/cost_eval.py
    python evals/cost_eval.py --models openai/gpt-4o-mini

הערה: אם compare_models.py כבר רץ, הריצות נלקחות מה-cache ולא עולות שום דבר נוסף.
"""

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from evals.common import DEFAULT_MODELS, RESULTS_DIR, TASKS, console, run_task_cached, write_csv  # noqa: E402

from rich.table import Table  # noqa: E402

from agent.llm import get_price, get_theoretical_price  # noqa: E402


def per_million(price_per_token: float) -> float:
    """מחירים מוצגים בדרך כלל לכל מיליון טוקנים."""
    return round(price_per_token * 1_000_000, 4)


def main() -> None:
    parser = argparse.ArgumentParser(description="Eval #2: cost and usage per model")
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    parser.add_argument("--tasks", nargs="+", default=list(TASKS), choices=list(TASKS))
    parser.add_argument("--fresh", action="store_true", help="ignore the cache and run everything again")
    args = parser.parse_args()

    console.rule("[bold]Eval #2: עלויות ושימוש")
    if not any(not m.endswith(":free") for m in args.models):
        console.print("[yellow]⚠️  אין ברשימה מודל בתשלום. הדרישה בפרויקט: לפחות מודל אחד בתשלום.[/yellow]")

    rows, node_rows = [], []
    for model in args.models:
        console.print(f"[bold cyan]{model}[/bold cyan]")
        pin, pout = get_price(model)
        tpin, tpout = get_theoretical_price(model)
        for task_id in args.tasks:
            r = run_task_cached(model, task_id, args.fresh)
            rows.append({
                "model": model,
                "task": task_id,
                "status": r["status"],
                "is_free": model.endswith(":free"),
                "llm_calls": r["calls"],
                "input_tokens": r["input_tokens"],
                "output_tokens": r["output_tokens"],
                "actual_cost_usd": round(r["actual_cost"], 6),
                "theoretical_cost_usd": round(r["theoretical_cost"], 6),
                "latency_s": r["latency_s"],
                "price_in_per_1M": per_million(pin),
                "price_out_per_1M": per_million(pout),
                "theoretical_price_in_per_1M": per_million(tpin),
                "theoretical_price_out_per_1M": per_million(tpout),
                "error": r["error"],
            })

            # פירוק לפי נוד: איזה שלב בגרף הכי יקר?
            by_node = defaultdict(lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0, "theoretical_cost": 0.0})
            for u in r.get("usage", []):
                n = by_node[u["node"]]
                n["calls"] += 1
                n["input_tokens"] += u["input_tokens"]
                n["output_tokens"] += u["output_tokens"]
                n["theoretical_cost"] += u["theoretical_cost"]
            for node, n in by_node.items():
                node_rows.append({"model": model, "task": task_id, "node": node, **n,
                                  "theoretical_cost": round(n["theoretical_cost"], 6)})

    write_csv(RESULTS_DIR / "cost_eval.csv", rows)
    write_csv(RESULTS_DIR / "cost_eval_by_node.csv", node_rows)

    # --- סיכום לכל מודל ---
    summary = []
    for model in args.models:
        ok = [r for r in rows if r["model"] == model and r["status"] == "ok"]
        if not ok:
            summary.append({"model": model, "tasks_ok": 0})
            continue
        total = lambda k: sum(r[k] for r in ok)  # noqa: E731
        per_task = total("theoretical_cost_usd") / len(ok)
        summary.append({
            "model": model,
            "is_free": model.endswith(":free"),
            "tasks_ok": len(ok),
            "llm_calls": total("llm_calls"),
            "input_tokens": total("input_tokens"),
            "output_tokens": total("output_tokens"),
            "actual_cost_usd": round(total("actual_cost_usd"), 6),
            "theoretical_cost_usd": round(total("theoretical_cost_usd"), 6),
            "avg_cost_per_task_usd": round(per_task, 6),
            "cost_per_1000_tasks_usd": round(per_task * 1000, 2),
            "avg_latency_s": round(total("latency_s") / len(ok), 1),
        })
    write_csv(RESULTS_DIR / "cost_eval_summary.csv", summary)

    table = Table(title="עלויות (סה\"כ על כל המשימות)")
    for col in ["model", "calls", "tokens in", "tokens out", "בפועל $", "תיאורטי $", "ל-1000 משימות $", "זמן ממוצע"]:
        table.add_column(col, justify="left" if col == "model" else "right")
    for s in summary:
        if not s["tasks_ok"]:
            table.add_row(s["model"], "[red]נכשל[/red]", *[""] * 6)
            continue
        table.add_row(s["model"], str(s["llm_calls"]), f"{s['input_tokens']:,}", f"{s['output_tokens']:,}",
                      f"{s['actual_cost_usd']:.4f}", f"{s['theoretical_cost_usd']:.4f}",
                      f"{s['cost_per_1000_tasks_usd']:.2f}", f"{s['avg_latency_s']}s")
    console.print(table)
    console.print("[dim]עלות תיאורטית = כמה היה עולה אילו המודל היה בתשלום. במודל בתשלום: זהה לעלות בפועל.[/dim]")


if __name__ == "__main__":
    main()
