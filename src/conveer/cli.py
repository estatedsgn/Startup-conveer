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

from . import config, storage
from .graph import build_graph
from .runner import SubordinateError

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
def validate(
    title: str = typer.Option(..., "--title", help="Idea title"),
    hypothesis: str = typer.Option(..., "--hypothesis", "-H", help="Falsifiable hypothesis"),
    audience: str = typer.Option("", "--audience", "-a", help="Target audience / ICP"),
    rationale: str = typer.Option("", "--rationale", help="Why it could work"),
    topic: str = typer.Option("", "--topic", "-t", help="Context topic (defaults to title)"),
) -> None:
    """Validate ONE owner-provided idea (skips generation & selection)."""
    from .graph import validate_single_idea

    run_id = storage.new_run_id()
    idea = {
        "id": "idea-owner",
        "title": title,
        "hypothesis": hypothesis,
        "target_audience": audience,
        "rationale": rationale,
    }
    console.print(f"[bold]Validating idea[/bold] {run_id}: {title!r}")
    try:
        state = validate_single_idea(idea, topic or title)
    except SubordinateError as exc:
        console.print(f"[bold red]Subordinate failed:[/bold red] {exc}")
        raise typer.Exit(code=1)
    state["run_id"] = run_id
    html_path = _finalize(run_id, state)
    _print_outcome(run_id, state, html_path)


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


@app.command()
def roster() -> None:
    """List all agents (the company headcount) with model and prompt version."""
    from . import prompt_store
    from .registry import CONTROL_PLANE, REGISTRY

    table = Table(show_header=True, header_style="bold", title="Conveer roster")
    table.add_column("role")
    table.add_column("title")
    table.add_column("tier")
    table.add_column("model")
    table.add_column("prompt")
    table.add_column("plane")
    for name, spec in REGISTRY.items():
        cur = prompt_store.current_prompt(name)
        plane = "control" if name in CONTROL_PLANE else "worker/dept"
        table.add_row(name, spec.title, spec.tier, spec.model(), cur.label, plane)
    console.print(table)


@app.command()
def improve(
    role: str = typer.Option(..., "--role", "-r", help="Role to train"),
    task: str = typer.Option(..., "--task", help="Representative task to exercise it"),
    rounds: int = typer.Option(config.IMPROVE_MAX_ROUNDS, "--rounds", help="Max improvement rounds"),
    bar: float = typer.Option(config.IMPROVE_BAR, "--bar", help="Pass threshold (0..1)"),
) -> None:
    """Train one agent: work -> critic -> coach rewrites its prompt -> repeat."""
    from .improve import improve_role
    from .registry import CONTROL_PLANE

    if role in CONTROL_PLANE:
        console.print(f"[yellow]{role} is control-plane and isn't self-trained.[/yellow]")
        raise typer.Exit(code=1)

    console.print(f"[bold]Training[/bold] {role} (bar={bar}, max rounds={rounds})")
    try:
        final = improve_role(role, task, bar=bar, max_rounds=rounds)
    except SubordinateError as exc:
        console.print(f"[bold red]Subordinate failed:[/bold red] {exc}")
        raise typer.Exit(code=1)

    table = Table(show_header=True, header_style="bold")
    table.add_column("round", justify="right")
    table.add_column("version")
    table.add_column("score", justify="right")
    table.add_column("passed")
    table.add_column("fundamental issues")
    for e in final.get("history", []):
        table.add_row(
            str(e.get("round")), str(e.get("version")), f"{e.get('score'):.2f}",
            "✅" if e.get("passed") else "❌",
            "; ".join(filter(None, e.get("issues", []))) or "—",
        )
    console.print(table)
    cost = final.get("cost", {})
    console.print(
        f"[green]Best version now active:[/green] v{final.get('selected_version')} "
        f"[dim]· cost {cost.get('tokens',0)} tokens / ${cost.get('usd',0)}[/dim]"
    )


@app.command(name="prompt-history")
def prompt_history(role: str = typer.Argument(..., help="Role to inspect")) -> None:
    """Show the evolution of a role's system prompt."""
    from . import prompt_store

    hist = prompt_store.history(role)
    cur = prompt_store.current_prompt(role)
    console.print(f"[bold]{role}[/bold] — current: {cur.label}")
    if not hist:
        console.print("[dim]No evolved versions yet (running on baseline).[/dim]")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("version")
    table.add_column("score", justify="right")
    table.add_column("created")
    table.add_column("rationale")
    for v in hist:
        score = v.get("score")
        table.add_row(
            f"v{v.get('version')}",
            f"{score:.2f}" if isinstance(score, (int, float)) else "—",
            str(v.get("created", "")),
            str(v.get("rationale", "")),
        )
    console.print(table)


@app.command(name="org")
def org_cmd() -> None:
    """Show the agent org chart (who delegates to whom)."""
    from . import org as orgmod
    from .registry import REGISTRY

    teams = orgmod.load_teams()
    if not teams:
        console.print("[yellow]No teams.yaml found.[/yellow]")
        return
    console.print("[bold]Org chart[/bold]")
    console.print(orgmod.render_tree(orgmod.tree(teams, "chief")))
    table = Table(show_header=True, header_style="bold", title="Agents in the org")
    table.add_column("role")
    table.add_column("kind")
    table.add_column("delegates_to")
    for role in sorted(orgmod.subtree_roles(teams, "chief")):
        spec = REGISTRY.get(role)
        table.add_row(role, spec.kind if spec else "?", ", ".join(orgmod.delegates_of(role, teams)) or "—")
    console.print(table)


@app.command(name="run-goal")
def run_goal_cmd(
    goal: str = typer.Argument(..., help="The goal to delegate through the agent tree"),
    root: str = typer.Option("chief", "--root", help="Top orchestrator to start from"),
    verify: bool = typer.Option(True, "--verify/--no-verify", help="Validator checks + fixes worker outputs (Factory loop)"),
) -> None:
    """Delegate a goal: chief -> leads -> workers, communicating via A2A."""
    from . import config as cfg
    from .orchestrate import run_goal

    console.print(f"[bold]Goal[/bold] -> {root}: {goal!r}")
    try:
        result = run_goal(goal, root=root, verify=verify)
    except SubordinateError as exc:
        console.print(f"[bold red]Agent failed:[/bold red] {exc}")
        raise typer.Exit(code=1)

    final = result.get("final", {})
    console.print("\n[bold green]Synthesis:[/bold green]")
    console.print(final.get("summary", "—"))
    for f in final.get("key_findings", []):
        console.print(f"  • {f}")
    if final.get("recommendation"):
        console.print(f"[bold]Recommendation:[/bold] {final['recommendation']}")
    cost = result.get("cost", {})
    console.print(
        f"[dim]cost: {cost.get('tokens',0)} tokens / ${cost.get('usd',0)} · "
        f"context {result.get('context_id')}[/dim]"
    )
    console.print(f"[bold]Workspace (agents live here):[/bold] {cfg.WORKSPACE_DIR}")


@app.command(name="prompts-dump")
def prompts_dump(
    out: str = typer.Option("exported_prompts", "--out", help="Output directory"),
) -> None:
    """Export every agent's full composed system prompt (role + team + A2A)."""
    from pathlib import Path

    from . import org as orgmod
    from . import prompt_builder, prompt_store
    from .registry import REGISTRY, is_orchestrator

    teams = orgmod.load_teams()
    in_org = orgmod.subtree_roles(teams, "chief") if teams else set()
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)

    for role in REGISTRY:
        if role in in_org:
            phase = "delegate" if is_orchestrator(role) else "worker"
            text = prompt_builder.build(role, teams=teams, phase=phase)
        else:
            text = prompt_store.current_prompt(role).text
        (out_dir / f"{role}.md").write_text(text, encoding="utf-8")

    console.print(f"[green]Exported {len(REGISTRY)} system prompts to[/green] {out_dir}/")


if __name__ == "__main__":
    app()
