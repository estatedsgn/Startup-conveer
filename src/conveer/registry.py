"""Role registry — the seed of the "agent conveyor".

Phase 0 declares roles inline. As the company grows, a new worker becomes a new
entry here (prompt + model tier + tool scope) with no plumbing changes, and
later this can be backed by a registry.yaml + A2A agent cards.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import config


@dataclass(frozen=True)
class RoleSpec:
    name: str
    title: str
    tier: str  # "lead" | "worker"
    # "orchestrator" (delegates/verifies) or "worker" (executes).
    kind: str = "worker"
    skills: tuple[str, ...] = ()
    # Tool scope (least privilege). None => default tools; [] => no tools.
    allowed_tools: list[str] | None = field(default=None)

    def model(self, settings: config.Settings | None = None) -> str:
        settings = settings or config.load_settings()
        return settings.model_for(self.name)


REGISTRY: dict[str, RoleSpec] = {
    # --- orchestrators (delegate, distribute, verify, aggregate) ---
    "chief": RoleSpec(
        "chief", "Chief Orchestrator", "lead", kind="orchestrator",
        skills=("decompose goals", "route", "verify", "synthesize"), allowed_tools=[],
    ),
    "research_lead": RoleSpec(
        "research_lead", "Research Lead", "lead", kind="orchestrator",
        skills=("market sizing", "competition", "validation design"), allowed_tools=[],
    ),
    "gtm_lead": RoleSpec(
        "gtm_lead", "Go-To-Market Lead", "lead", kind="orchestrator",
        skills=("channels", "messaging", "campaign planning"), allowed_tools=[],
    ),
    # --- department #1: idea generation + initial testing ---
    "idea_generator": RoleSpec(
        "idea_generator", "Idea Generator", "worker", skills=("ideation",), allowed_tools=[]
    ),
    "analyst": RoleSpec(
        "analyst", "Research Analyst", "lead", skills=("test design", "metrics"), allowed_tools=[]
    ),
    "reporter": RoleSpec("reporter", "Reporter", "worker", skills=("reporting",), allowed_tools=[]),
    "ceo": RoleSpec("ceo", "CEO (decision)", "lead", skills=("go/no-go",), allowed_tools=[]),
    # --- expanded roster (workers) ---
    "market_researcher": RoleSpec(
        "market_researcher", "Market Researcher", "worker", skills=("market sizing",), allowed_tools=[]
    ),
    "competitor_analyst": RoleSpec(
        "competitor_analyst", "Competitor Analyst", "worker", skills=("competition",), allowed_tools=[]
    ),
    "copywriter": RoleSpec("copywriter", "Copywriter", "worker", skills=("copy",), allowed_tools=[]),
    "outreach_planner": RoleSpec(
        "outreach_planner", "Outreach Planner", "worker", skills=("gtm planning",), allowed_tools=[]
    ),
    # --- control / self-improvement plane ---
    "critic": RoleSpec("critic", "Critic / QA", "lead", skills=("evaluation",), allowed_tools=[]),
    "coach": RoleSpec("coach", "Coach", "lead", skills=("prompt improvement",), allowed_tools=[]),
}

# Roles that form the control plane (not improvable by themselves to avoid
# the loop rewriting its own judge/teacher unsupervised).
CONTROL_PLANE = {"critic", "coach"}


def improvable_roles() -> list[str]:
    return [name for name in REGISTRY if name not in CONTROL_PLANE]


def get_role(name: str) -> RoleSpec:
    if name not in REGISTRY:
        raise KeyError(f"Unknown role '{name}'. Known: {sorted(REGISTRY)}")
    return REGISTRY[name]


def is_orchestrator(name: str) -> bool:
    return name in REGISTRY and REGISTRY[name].kind == "orchestrator"


def agent_card(name: str, delegates_to: list[str] | None = None):
    """Build an A2A Agent Card for a role (its passport)."""
    from .a2a import AgentCard

    spec = get_role(name)
    return AgentCard(
        name=spec.name, title=spec.title, kind=spec.kind,
        description=f"{spec.title} ({spec.tier} tier)",
        skills=list(spec.skills), model=spec.model(),
        delegates_to=delegates_to or [],
    )
