"""Conveer CLI — drive department #1 with human-in-the-loop gates.

Commands:
  conveer run --topic "..." [--ideas 6]   start a new validation run
  conveer resume <run_id>                 resume a paused run
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from . import config, registry, storage
from .graph import build_graph, extract_json
from .runner import SubordinateError, run_subordinate

load_dotenv()
app = typer.Typer(add_completion=False, help="Startup-Conveer — AI company conveyor.")
console = Console()


# --------------------------------------------------------------------------- #
# Human-in-the-loop interrupt handlers
# --------------------------------------------------------------------------- #
def _handle_select(payload: dict[str, Any]) -> list[str]:
    console.print(f"\n[bold]{payload.get('instruction','Select ideas')}[/bold]")
    table = Table(show_header=True, header_style="bold")
    table.add_column("#", justify="right")
    table.add_column("id")
    table.add_column("title")
    ideas = payload.get("ideas", [])
    for n, i in enumerate(ideas, 1):
        table.add_row(str(n), str(i.get("id")), str(i.get("title")))
    console.print(table)
    raw = typer.prompt("Enter 3 ids or numbers, comma-separated")
    return [s.strip() for s in raw.split(",") if s.strip()]


def _handle_results(payload: dict[str, Any]) -> dict[str, str]:
    console.print(f"\n[bold]{payload.get('instruction','Enter results')}[/bold]")
    skip = {"__skip__": True}  # non-falsy sentinel; LangGraph ignores falsy resumes
    if not typer.confirm("Enter customer-discovery results now?", default=False):
        console.print("[dim]Skipping — report will say 'no data yet'.[/dim]")
        return skip
    results: dict[str, str] = {}
    for idea_id in payload.get("idea_ids", []):
        text = typer.prompt(f"Results for {idea_id} (blank to skip)", default="")
        if text.strip():
            results[str(idea_id)] = text.strip()
    return results or skip


def _handle_interrupt(payload: dict[str, Any]) -> Any:
    kind = payload.get("type")
    if kind == "select_ideas":
        return _handle_select(payload)
    if kind == "enter_custdev_results":
        return _handle_results(payload)
    raise typer.BadParameter(f"Unknown interrupt type: {kind}")


# --------------------------------------------------------------------------- #
# Drive loop
# --------------------------------------------------------------------------- #
def _drive(graph, first_input: Any, cfg: dict) -> dict[str, Any]:
    from langgraph.types import Command

    result = graph.invoke(first_input, cfg)
    while isinstance(result, dict) and result.get("__interrupt__"):
        payload = result["__interrupt__"][0].value
        resume_value = _handle_interrupt(payload)
        result = graph.invoke(Command(resume=resume_value), cfg)
    return result


def _finalize(run_id: str, state: dict[str, Any]) -> Path:
    from .report import render_html

    storage.save_json(run_id, "ideas.json", state.get("ideas", []))
    storage.save_json(run_id, "methodologies.json", state.get("methodologies", []))
    storage.save_text(run_id, "report.md", state.get("report_md", ""))
    storage.save_json(run_id, "decision.json", state.get("decision", {}))
    # phase 1: venture studio artifacts (present only if the CEO green-lit ideas)
    if state.get("execution_plan"):
        storage.save_json(run_id, "market_research.json", state.get("market_research", []))
        storage.save_json(run_id, "product_plans.json", state.get("product_plans", []))
        storage.save_json(run_id, "tech_assessments.json", state.get("tech_assessments", []))
        storage.save_json(run_id, "gtm_plans.json", state.get("gtm_plans", []))
        storage.save_json(run_id, "finance_models.json", state.get("finance_models", []))
        storage.save_json(run_id, "execution_plan.json", state.get("execution_plan", {}))
    storage.save_json(run_id, "run.json", {
        "run_id": run_id, "topic": state.get("topic"),
        "cost": state.get("cost", {}), "log": state.get("log", []),
    })
    html_path = storage.run_dir(run_id) / "report.html"
    html_path.write_text(render_html(state), encoding="utf-8")
    return html_path


def _print_outcome(run_id: str, state: dict[str, Any], html_path: Path) -> None:
    decision = state.get("decision", {}) or {}
    cost = state.get("cost", {}) or {}
    console.print(f"\n[bold green]Run complete:[/bold green] {run_id}")
    console.print(f"[bold]CEO:[/bold] {decision.get('summary','—')}")
    for d in decision.get("decisions", []):
        console.print(
            f"  • {d.get('idea_id')} [{d.get('decision')}] — {d.get('next_step','')}"
        )
    plan = state.get("execution_plan", {}) or {}
    if plan:
        console.print(f"\n[bold]COO:[/bold] {plan.get('summary','—')}")
        console.print(f"[bold]Focus:[/bold] {plan.get('recommended_focus','—')}")
        for v in plan.get("ventures", []):
            console.print(
                f"  • {v.get('idea_id')} [{v.get('readiness')}] — owner: {v.get('owner','?')}"
            )
    console.print(f"[dim]cost: {cost.get('tokens',0)} tokens / ${cost.get('usd',0)}[/dim]")
    console.print(f"[bold]Report:[/bold] {html_path}")


def _cfg(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id},
            "recursion_limit": config.RECURSION_LIMIT}


def _run_with_saver(run_id: str, make_first_input) -> None:
    """Open the durable saver, build the graph, drive it, persist artifacts.

    `make_first_input(graph, cfg)` returns the value to feed `_drive` (a fresh
    input dict for a new run, or a Command for resuming).
    """
    from langgraph.checkpoint.sqlite import SqliteSaver

    config.CHECKPOINT_DB.parent.mkdir(parents=True, exist_ok=True)
    cfg = _cfg(run_id)
    with SqliteSaver.from_conn_string(str(config.CHECKPOINT_DB)) as saver:
        graph = build_graph(saver)
        first_input = make_first_input(graph, cfg)
        if first_input is None:
            return
        try:
            state = _drive(graph, first_input, cfg)
        except SubordinateError as exc:
            console.print(f"[bold red]Subordinate failed:[/bold red] {exc}")
            raise typer.Exit(code=1)
    html_path = _finalize(run_id, state)
    _print_outcome(run_id, state, html_path)


@app.command()
def run(
    topic: str = typer.Option(..., "--topic", "-t", help="Topic / niche to explore"),
    ideas: int = typer.Option(6, "--ideas", "-n", help="How many ideas to generate"),
) -> None:
    """Start a new validation run for department #1."""
    run_id = storage.new_run_id()
    console.print(f"[bold]Starting run[/bold] {run_id} — topic: {topic!r}")
    _run_with_saver(
        run_id,
        lambda g, c: {"run_id": run_id, "topic": topic, "num_ideas": ideas},
    )


@app.command()
def resume(run_id: str = typer.Argument(..., help="Run id to resume")) -> None:
    """Resume a paused run (e.g. after collecting custdev results)."""
    from langgraph.types import Command

    console.print(f"[bold]Resuming[/bold] {run_id}")

    def make_first(graph, cfg):
        snap = graph.get_state(cfg)
        if not snap.next:
            console.print("[yellow]Nothing to resume — run is complete.[/yellow]")
            return None
        if snap.interrupts:
            resume_value = _handle_interrupt(snap.interrupts[0].value)
            return Command(resume=resume_value)
        return Command(resume=None)

    _run_with_saver(run_id, make_first)


def _print_hustle(plan: dict[str, Any]) -> None:
    console.print(f"\n[bold]Operator:[/bold] {plan.get('situation_readback','—')}")
    for i, p in enumerate(plan.get("money_plays", []), 1):
        console.print(f"\n[bold cyan]{i}. {p.get('name','play')}[/bold cyan] — "
                      f"{p.get('what_it_is','')}")
        console.print(f"   [dim]why you:[/dim] {p.get('why_you','')}")
        for s in p.get("steps_today", []):
            console.print(f"     • {s}")
        console.print(
            f"   [dim]first $:[/dim] {p.get('first_dollar_path','')} "
            f"[dim]| eta[/dim] {p.get('time_to_first_revenue','?')} "
            f"[dim]| wk1[/dim] {p.get('expected_week1_usd','?')} "
            f"[dim]| cost[/dim] {p.get('startup_cost_usd','?')}"
        )
    do_now = plan.get("do_this_now", [])
    if do_now:
        console.print("\n[bold green]DO THIS NOW:[/bold green]")
        for n, s in enumerate(do_now, 1):
            console.print(f"  {n}. {s}")
    if plan.get("reality_check"):
        console.print(f"\n[bold yellow]Reality check:[/bold yellow] {plan['reality_check']}")
    for x in plan.get("needs_human", []):
        console.print(f"[dim]needs you:[/dim] {x}")


@app.command()
def hustle(
    skills: str = typer.Option(..., "--skills", "-s", help="Your skills/assets"),
    hours: float = typer.Option(2.0, "--hours", "-h", help="Hours/day you can put in"),
    budget: float = typer.Option(0.0, "--budget", "-b", help="Starting budget (USD)"),
    payout: str = typer.Option("", "--payout", help="How you can get paid (e.g. PayPal)"),
    goal: str = typer.Option("first real revenue this week", "--goal", "-g",
                             help="Your money goal"),
) -> None:
    """Ask the Operator what to do today to start earning. You do the doing."""
    run_id = storage.new_run_id()
    prompt = (
        "Owner's real situation — build the money plan:\n"
        f"- skills/assets: {skills}\n"
        f"- time per day: {hours} hours\n"
        f"- starting budget: ${budget}\n"
        f"- payout method: {payout or 'unspecified'}\n"
        f"- goal: {goal}\n"
    )
    spec = registry.get_role("operator")
    console.print(f"[bold]Operator working[/bold] (run {run_id})…")
    try:
        res = run_subordinate("operator", prompt, allowed_tools=spec.allowed_tools)
    except SubordinateError as exc:
        console.print(f"[bold red]Operator failed:[/bold red] {exc}")
        raise typer.Exit(code=1)
    plan = extract_json(res.text, "object")
    storage.save_json(run_id, "hustle_plan.json", plan)
    _print_hustle(plan)
    console.print(f"\n[dim]cost: {res.total_tokens} tokens / "
                  f"${round(res.cost_usd, 4)} · saved to runs/{run_id}/[/dim]")


if __name__ == "__main__":
    app()
