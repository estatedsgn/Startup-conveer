"""Graph state for department #1 (idea generation + initial testing)."""

from __future__ import annotations

from typing import Any, TypedDict


class DeptState(TypedDict, total=False):
    """Shared state threaded through the LangGraph nodes.

    Each node reads what it needs and returns a partial update.
    """

    run_id: str
    topic: str
    num_ideas: int

    ideas: list[dict[str, Any]]          # all generated ideas (Idea-shaped dicts)
    selected_ideas: list[dict[str, Any]]  # the 3 picked by the human
    methodologies: list[dict[str, Any]]   # analyst output per selected idea
    custdev_results: dict[str, str]       # idea_id -> raw results pasted by human

    report_md: str                        # reporter output
    decision: dict[str, Any]              # CEO go/no-go per idea + summary

    sessions: dict[str, str]              # role -> claude session_id (continuity)
    cost: dict[str, float]                # {"tokens": .., "usd": ..} accumulator
    log: list[str]                        # human-readable trace of steps
