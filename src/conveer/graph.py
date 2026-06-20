"""LangGraph orchestration for department #1.

Flow:
  idea_generator -> [interrupt: pick 3] -> analyst (per idea)
  -> [interrupt: paste custdev results] -> reporter -> ceo -> END

Human-in-the-loop points use LangGraph `interrupt()`; the run is checkpointed so
it can be resumed (`conveer resume <run_id>`).
"""

from __future__ import annotations

import json
import re
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from . import config
from .observability import traceable
from .runner import RunResult, run_subordinate
from .state import DeptState


# --------------------------------------------------------------------------- #
# Subordinate seam (single instrumented entry point; mocked in tests)
# --------------------------------------------------------------------------- #
@traceable(name="subordinate")
def invoke_role(
    role: str,
    prompt: str,
    *,
    session_id: str | None = None,
    settings: config.Settings | None = None,
) -> RunResult:
    return run_subordinate(role, prompt, session_id=session_id, settings=settings)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _strip_fences(text: str) -> str:
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    return fence.group(1).strip() if fence else text


def extract_json(text: str, kind: str) -> Any:
    """Extract a JSON array ('[') or object ('{') from model text, tolerantly."""
    open_ch, close_ch = ("[", "]") if kind == "array" else ("{", "}")
    cleaned = _strip_fences(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find(open_ch)
        end = cleaned.rfind(close_ch)
        if start != -1 and end != -1 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


def _accumulate_cost(state: DeptState, *results: RunResult) -> dict[str, float]:
    cur = state.get("cost", {}) or {}
    tokens = int(cur.get("tokens", 0))
    usd = float(cur.get("usd", 0.0))
    for r in results:
        tokens += r.total_tokens
        usd += r.cost_usd
    return {"tokens": tokens, "usd": round(usd, 6)}


def _append_log(state: DeptState, *msgs: str) -> list[str]:
    return list(state.get("log", [])) + list(msgs)


def _merge_sessions(state: DeptState, role: str, result: RunResult) -> dict[str, str]:
    sessions = dict(state.get("sessions", {}))
    if result.session_id:
        sessions[role] = result.session_id
    return sessions


def resolve_selection(ideas: list[dict], selection: Any) -> list[dict]:
    """Resolve a human selection (ids or 1-based indices) to idea dicts."""
    by_id = {str(i.get("id")): i for i in ideas}
    chosen: list[dict] = []
    for item in selection or []:
        key = str(item).strip()
        if key in by_id:
            chosen.append(by_id[key])
        elif key.isdigit() and 1 <= int(key) <= len(ideas):
            chosen.append(ideas[int(key) - 1])
    return chosen


# --------------------------------------------------------------------------- #
# Nodes
# --------------------------------------------------------------------------- #
def node_idea_generator(state: DeptState) -> dict[str, Any]:
    settings = config.load_settings()
    topic = state["topic"]
    n = int(state.get("num_ideas", 6))
    prompt = f"Topic/niche: {topic}\nGenerate {n} testable ideas as specified."
    res = invoke_role("idea_generator", prompt, settings=settings)
    ideas = extract_json(res.text, "array")
    return {
        "ideas": ideas,
        "sessions": _merge_sessions(state, "idea_generator", res),
        "cost": _accumulate_cost(state, res),
        "log": _append_log(state, f"idea_generator -> {len(ideas)} ideas"),
    }


def node_select(state: DeptState) -> dict[str, Any]:
    ideas = state.get("ideas", [])
    selection = interrupt(
        {
            "type": "select_ideas",
            "instruction": "Choose exactly 3 idea ids (or indices) to test.",
            "ideas": [
                {"id": i.get("id"), "title": i.get("title")} for i in ideas
            ],
        }
    )
    selected = resolve_selection(ideas, selection)
    return {
        "selected_ideas": selected,
        "log": _append_log(state, f"human selected {len(selected)} ideas"),
    }


def node_analyst(state: DeptState) -> dict[str, Any]:
    settings = config.load_settings()
    methodologies: list[dict] = []
    sessions = dict(state.get("sessions", {}))
    results: list[RunResult] = []
    for idea in state.get("selected_ideas", []):
        prompt = (
            "Design the validation for this ONE idea:\n"
            + json.dumps(idea, ensure_ascii=False)
        )
        res = invoke_role("analyst", prompt, settings=settings)
        method = extract_json(res.text, "object")
        method.setdefault("idea_id", idea.get("id"))
        methodologies.append(method)
        results.append(res)
        if res.session_id:
            sessions["analyst"] = res.session_id
    return {
        "methodologies": methodologies,
        "sessions": sessions,
        "cost": _accumulate_cost(state, *results),
        "log": _append_log(state, f"analyst -> {len(methodologies)} test designs"),
    }


def node_enter_results(state: DeptState) -> dict[str, Any]:
    selected = state.get("selected_ideas", [])
    payload = interrupt(
        {
            "type": "enter_custdev_results",
            "instruction": (
                "Paste customer-discovery results per idea id "
                '(dict idea_id -> text). Send {"__skip__": true} to skip for now.'
            ),
            "idea_ids": [i.get("id") for i in selected],
        }
    )
    # NOTE: LangGraph ignores a falsy resume value (e.g. {}), so skipping is
    # signalled with a non-empty sentinel that we strip here.
    results = dict(payload) if isinstance(payload, dict) else {}
    results.pop("__skip__", None)
    return {
        "custdev_results": results,
        "log": _append_log(state, f"custdev results for {len(results)} ideas"),
    }


def node_reporter(state: DeptState) -> dict[str, Any]:
    ctx = {
        "topic": state.get("topic"),
        "selected_ideas": state.get("selected_ideas", []),
        "methodologies": state.get("methodologies", []),
        "custdev_results": state.get("custdev_results", {}),
    }
    prompt = "Build the validation report from this data:\n" + json.dumps(
        ctx, ensure_ascii=False, indent=2
    )
    res = invoke_role("reporter", prompt)
    return {
        "report_md": res.text,
        "sessions": _merge_sessions(state, "reporter", res),
        "cost": _accumulate_cost(state, res),
        "log": _append_log(state, "reporter -> report.md"),
    }


def node_ceo(state: DeptState) -> dict[str, Any]:
    prompt = (
        "Validation report:\n"
        + state.get("report_md", "")
        + "\n\nDecide go/no-go per idea as specified."
    )
    res = invoke_role("ceo", prompt)
    decision = extract_json(res.text, "object")
    return {
        "decision": decision,
        "sessions": _merge_sessions(state, "ceo", res),
        "cost": _accumulate_cost(state, res),
        "log": _append_log(state, "ceo -> decision"),
    }


# --------------------------------------------------------------------------- #
# Phase 1: Venture Studio — plan the green-lit ideas into execution
# --------------------------------------------------------------------------- #
def select_greenlit(state: DeptState) -> list[dict]:
    """Ideas the CEO marked 'go', resolved back to their full idea dicts."""
    decision = state.get("decision", {}) or {}
    go_ids = {
        str(d.get("idea_id"))
        for d in decision.get("decisions", [])
        if str(d.get("decision", "")).lower() == "go"
    }
    return [i for i in state.get("selected_ideas", []) if str(i.get("id")) in go_ids]


def _methodology_for(state: DeptState, idea_id: Any) -> dict:
    for m in state.get("methodologies", []):
        if str(m.get("idea_id")) == str(idea_id):
            return m
    return {}


def _plan_per_idea(
    state: DeptState, role: str, objective: str
) -> tuple[list[dict], dict[str, str], list[RunResult]]:
    """Run one specialist `role` over every green-lit idea (analyst-style loop).

    Each call gets the idea plus the discovery context we already have, so the
    specialist plans against real signal, not just a title.
    """
    settings = config.load_settings()
    out: list[dict] = []
    sessions = dict(state.get("sessions", {}))
    results: list[RunResult] = []
    custdev = state.get("custdev_results", {}) or {}
    for idea in state.get("greenlit_ideas", []):
        ctx = {
            "idea": idea,
            "methodology": _methodology_for(state, idea.get("id")),
            "custdev_results": custdev.get(str(idea.get("id")), ""),
        }
        prompt = objective + "\n" + json.dumps(ctx, ensure_ascii=False)
        res = invoke_role(role, prompt, settings=settings)
        obj = extract_json(res.text, "object")
        obj.setdefault("idea_id", idea.get("id"))
        out.append(obj)
        results.append(res)
        if res.session_id:
            sessions[role] = res.session_id
    return out, sessions, results


def node_greenlight(state: DeptState) -> dict[str, Any]:
    greenlit = select_greenlit(state)
    return {
        "greenlit_ideas": greenlit,
        "log": _append_log(state, f"greenlight -> {len(greenlit)} idea(s) to plan"),
    }


def route_after_greenlight(state: DeptState) -> str:
    """Only spin up the venture studio if the CEO actually green-lit something."""
    return "market_researcher" if state.get("greenlit_ideas") else END


def node_market_researcher(state: DeptState) -> dict[str, Any]:
    out, sessions, results = _plan_per_idea(
        state, "market_researcher", "Analyze the market for this green-lit idea:"
    )
    return {
        "market_research": out,
        "sessions": sessions,
        "cost": _accumulate_cost(state, *results),
        "log": _append_log(state, f"market_researcher -> {len(out)} analyses"),
    }


def node_product_manager(state: DeptState) -> dict[str, Any]:
    out, sessions, results = _plan_per_idea(
        state, "product_manager", "Define the MVP for this green-lit idea:"
    )
    return {
        "product_plans": out,
        "sessions": sessions,
        "cost": _accumulate_cost(state, *results),
        "log": _append_log(state, f"product_manager -> {len(out)} MVP plans"),
    }


def node_tech_lead(state: DeptState) -> dict[str, Any]:
    out, sessions, results = _plan_per_idea(
        state, "tech_lead", "Assess technical feasibility for this green-lit idea:"
    )
    return {
        "tech_assessments": out,
        "sessions": sessions,
        "cost": _accumulate_cost(state, *results),
        "log": _append_log(state, f"tech_lead -> {len(out)} assessments"),
    }


def node_growth_marketer(state: DeptState) -> dict[str, Any]:
    out, sessions, results = _plan_per_idea(
        state, "growth_marketer", "Design the go-to-market for this green-lit idea:"
    )
    return {
        "gtm_plans": out,
        "sessions": sessions,
        "cost": _accumulate_cost(state, *results),
        "log": _append_log(state, f"growth_marketer -> {len(out)} GTM plans"),
    }


def node_finance(state: DeptState) -> dict[str, Any]:
    out, sessions, results = _plan_per_idea(
        state, "finance", "Model the economics for this green-lit idea:"
    )
    return {
        "finance_models": out,
        "sessions": sessions,
        "cost": _accumulate_cost(state, *results),
        "log": _append_log(state, f"finance -> {len(out)} models"),
    }


def node_coo(state: DeptState) -> dict[str, Any]:
    ctx = {
        "topic": state.get("topic"),
        "greenlit_ideas": state.get("greenlit_ideas", []),
        "market_research": state.get("market_research", []),
        "product_plans": state.get("product_plans", []),
        "tech_assessments": state.get("tech_assessments", []),
        "gtm_plans": state.get("gtm_plans", []),
        "finance_models": state.get("finance_models", []),
        "ceo_decision": state.get("decision", {}),
    }
    prompt = "Fuse these specialist plans into ONE execution plan:\n" + json.dumps(
        ctx, ensure_ascii=False, indent=2
    )
    res = invoke_role("coo", prompt)
    plan = extract_json(res.text, "object")
    return {
        "execution_plan": plan,
        "sessions": _merge_sessions(state, "coo", res),
        "cost": _accumulate_cost(state, res),
        "log": _append_log(state, "coo -> execution plan"),
    }


# --------------------------------------------------------------------------- #
# Graph assembly
# --------------------------------------------------------------------------- #
def build_graph(checkpointer: Any = None):
    """Compile the department #1 graph. Pass a checkpointer for durable runs."""
    if checkpointer is None:
        from langgraph.checkpoint.memory import MemorySaver

        checkpointer = MemorySaver()

    g = StateGraph(DeptState)
    # department #1: idea generation + initial testing
    g.add_node("idea_generator", node_idea_generator)
    g.add_node("select", node_select)
    g.add_node("analyst", node_analyst)
    g.add_node("enter_results", node_enter_results)
    g.add_node("reporter", node_reporter)
    g.add_node("ceo", node_ceo)
    # phase 1: venture studio (runs only for green-lit ideas)
    g.add_node("greenlight", node_greenlight)
    g.add_node("market_researcher", node_market_researcher)
    g.add_node("product_manager", node_product_manager)
    g.add_node("tech_lead", node_tech_lead)
    g.add_node("growth_marketer", node_growth_marketer)
    g.add_node("finance", node_finance)
    g.add_node("coo", node_coo)

    g.add_edge(START, "idea_generator")
    g.add_edge("idea_generator", "select")
    g.add_edge("select", "analyst")
    g.add_edge("analyst", "enter_results")
    g.add_edge("enter_results", "reporter")
    g.add_edge("reporter", "ceo")
    g.add_edge("ceo", "greenlight")
    # gate: plan the venture only if the CEO green-lit at least one idea
    g.add_conditional_edges(
        "greenlight",
        route_after_greenlight,
        {"market_researcher": "market_researcher", END: END},
    )
    g.add_edge("market_researcher", "product_manager")
    g.add_edge("product_manager", "tech_lead")
    g.add_edge("tech_lead", "growth_marketer")
    g.add_edge("growth_marketer", "finance")
    g.add_edge("finance", "coo")
    g.add_edge("coo", END)

    return g.compile(checkpointer=checkpointer)
