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

    # --- phase 1: venture studio (only runs for green-lit ideas) ---
    greenlit_ideas: list[dict[str, Any]]  # ideas the CEO marked "go"
    market_research: list[dict[str, Any]]  # market_researcher output per idea
    product_plans: list[dict[str, Any]]    # product_manager output per idea
    tech_assessments: list[dict[str, Any]]  # tech_lead output per idea
    gtm_plans: list[dict[str, Any]]         # growth_marketer output per idea
    finance_models: list[dict[str, Any]]    # finance output per idea
    execution_plan: dict[str, Any]          # COO consolidated plan

    sessions: dict[str, str]              # role -> claude session_id (continuity)
    cost: dict[str, float]                # {"tokens": .., "usd": ..} accumulator
    log: list[str]                        # human-readable trace of steps
