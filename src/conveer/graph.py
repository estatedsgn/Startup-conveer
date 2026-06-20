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
# Graph assembly
# --------------------------------------------------------------------------- #
def build_graph(checkpointer: Any = None):
    """Compile the department #1 graph. Pass a checkpointer for durable runs."""
    if checkpointer is None:
        from langgraph.checkpoint.memory import MemorySaver

        checkpointer = MemorySaver()

    g = StateGraph(DeptState)
    g.add_node("idea_generator", node_idea_generator)
    g.add_node("select", node_select)
    g.add_node("analyst", node_analyst)
    g.add_node("enter_results", node_enter_results)
    g.add_node("reporter", node_reporter)
    g.add_node("ceo", node_ceo)

    g.add_edge(START, "idea_generator")
    g.add_edge("idea_generator", "select")
    g.add_edge("select", "analyst")
    g.add_edge("analyst", "enter_results")
    g.add_edge("enter_results", "reporter")
    g.add_edge("reporter", "ceo")
    g.add_edge("ceo", END)

    return g.compile(checkpointer=checkpointer)
