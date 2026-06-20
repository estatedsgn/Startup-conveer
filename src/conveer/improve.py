"""Self-improvement loop — how agents learn.

A target role is exercised on a representative task; the **Critic** scores the
output and names *fundamental* issues; if it's below the bar, the **Coach**
rewrites the role's system prompt at the level of principles (never by hardcoding
the example) and saves it as a new version. Then we re-test. The best-scoring
version is made current at the end.

Built as a LangGraph loop:  work -> critic -> (coach -> work)* -> END
"""

from __future__ import annotations

import json
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from . import config, graph, prompt_store


class ImproveState(TypedDict, total=False):
    role: str
    task: str
    bar: float
    max_rounds: int
    round: int
    output: str
    score: float
    critique: dict[str, Any]
    history: list[dict[str, Any]]
    cost: dict[str, float]


def _accumulate(state: ImproveState, *results) -> dict[str, float]:
    cur = state.get("cost", {}) or {}
    tokens = int(cur.get("tokens", 0))
    usd = float(cur.get("usd", 0.0))
    for r in results:
        tokens += r.total_tokens
        usd += r.cost_usd
    return {"tokens": tokens, "usd": round(usd, 6)}


def build_improve_graph(settings: config.Settings, checkpointer: Any = None):
    """Compile the improvement loop for a target role (settings via closure)."""

    def node_work(state: ImproveState) -> dict[str, Any]:
        role, task = state["role"], state["task"]
        res = graph.invoke_role(role, task, settings=settings)
        return {"output": res.text, "cost": _accumulate(state, res)}

    def node_critic(state: ImproveState) -> dict[str, Any]:
        role = state["role"]
        cur = prompt_store.current_prompt(role, settings)
        prompt = (
            f"Target role: {role}\n"
            f"Bar to pass: {state.get('bar')}\n\n"
            f"--- ROLE SYSTEM PROMPT (its job spec) ---\n{cur.text}\n\n"
            f"--- TASK GIVEN ---\n{state['task']}\n\n"
            f"--- OUTPUT PRODUCED ---\n{state.get('output','')}\n\n"
            "Evaluate per your instructions."
        )
        res = graph.invoke_role("critic", prompt, settings=settings)
        critique = graph.extract_json(res.text, "object")
        score = float(critique.get("score", 0.0) or 0.0)
        rnd = int(state.get("round", 0))
        entry = {
            "round": rnd,
            "version": cur.label,
            "score": score,
            "passed": score >= float(state.get("bar", config.IMPROVE_BAR)),
            "issues": [i.get("principle") for i in critique.get("fundamental_issues", [])],
        }
        return {
            "score": score,
            "critique": critique,
            "history": list(state.get("history", [])) + [entry],
            "cost": _accumulate(state, res),
        }

    def node_coach(state: ImproveState) -> dict[str, Any]:
        role = state["role"]
        cur = prompt_store.current_prompt(role, settings)
        critique = state.get("critique", {})
        prompt = (
            f"Target role: {role}\n\n"
            f"--- CURRENT SYSTEM PROMPT ---\n{cur.text}\n\n"
            f"--- TASK ---\n{state['task']}\n\n"
            f"--- OUTPUT ---\n{state.get('output','')}\n\n"
            f"--- CRITIC DIAGNOSIS ---\n{json.dumps(critique, ensure_ascii=False)}\n\n"
            "Rewrite the system prompt fundamentally per your instructions."
        )
        res = graph.invoke_role("coach", prompt, settings=settings)
        proposal = graph.extract_json(res.text, "object")
        new_prompt = proposal.get("improved_prompt", "").strip()
        if new_prompt:
            prompt_store.save_version(
                role,
                new_prompt,
                score=state.get("score"),
                rationale=proposal.get("change_rationale", ""),
                settings=settings,
            )
        return {
            "round": int(state.get("round", 0)) + 1,
            "cost": _accumulate(state, res),
        }

    def route(state: ImproveState) -> str:
        bar = float(state.get("bar", config.IMPROVE_BAR))
        max_rounds = int(state.get("max_rounds", config.IMPROVE_MAX_ROUNDS))
        if state.get("score", 0.0) >= bar:
            return "stop"
        if int(state.get("round", 0)) >= max_rounds:
            return "stop"
        return "coach"

    g = StateGraph(ImproveState)
    g.add_node("work", node_work)
    g.add_node("critic", node_critic)
    g.add_node("coach", node_coach)
    g.add_edge(START, "work")
    g.add_edge("work", "critic")
    g.add_conditional_edges("critic", route, {"coach": "coach", "stop": END})
    g.add_edge("coach", "work")

    if checkpointer is None:
        from langgraph.checkpoint.memory import MemorySaver

        checkpointer = MemorySaver()
    return g.compile(checkpointer=checkpointer)


def improve_role(
    role: str,
    task: str,
    *,
    bar: float | None = None,
    max_rounds: int | None = None,
    settings: config.Settings | None = None,
    thread_id: str | None = None,
) -> dict[str, Any]:
    """Run the improvement loop and make the best-scoring version current."""
    settings = settings or config.load_settings()
    bar = config.IMPROVE_BAR if bar is None else bar
    max_rounds = config.IMPROVE_MAX_ROUNDS if max_rounds is None else max_rounds

    g = build_improve_graph(settings)
    cfg = {
        "configurable": {"thread_id": thread_id or f"improve-{role}"},
        "recursion_limit": config.RECURSION_LIMIT,
    }
    final = g.invoke(
        {"role": role, "task": task, "bar": bar, "max_rounds": max_rounds, "round": 0},
        cfg,
    )

    # Pick the best version seen and make it current (rollback if a later
    # rewrite scored worse). Map history labels -> version numbers.
    best = _select_best_version(role, final.get("history", []), settings)
    if best is not None:
        prompt_store.set_current(role, best, settings)
    final["selected_version"] = best
    return final


def _select_best_version(
    role: str, history: list[dict], settings: config.Settings
) -> int | None:
    """Return the version number with the highest score from this run."""
    if not history:
        return None
    best_entry = max(history, key=lambda e: e.get("score", 0.0))
    label = best_entry.get("version", "v0-baseline")
    if label == "v0-baseline":
        return 0
    try:
        return int(label.lstrip("v"))
    except ValueError:
        return None
