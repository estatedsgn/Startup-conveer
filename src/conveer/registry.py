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
    # Tool scope (least privilege). None => default tools; [] => no tools.
    allowed_tools: list[str] | None = field(default=None)

    def model(self, settings: config.Settings | None = None) -> str:
        settings = settings or config.load_settings()
        return settings.model_for(self.name)


REGISTRY: dict[str, RoleSpec] = {
    # --- department #1: idea generation + initial testing ---
    "idea_generator": RoleSpec(
        "idea_generator", "Idea Generator", "worker", allowed_tools=[]
    ),
    "analyst": RoleSpec("analyst", "Research Analyst", "lead", allowed_tools=[]),
    "reporter": RoleSpec("reporter", "Reporter", "worker", allowed_tools=[]),
    "ceo": RoleSpec("ceo", "CEO", "lead", allowed_tools=[]),
    # --- expanded roster (workers) ---
    "market_researcher": RoleSpec(
        "market_researcher", "Market Researcher", "worker", allowed_tools=[]
    ),
    "competitor_analyst": RoleSpec(
        "competitor_analyst", "Competitor Analyst", "worker", allowed_tools=[]
    ),
    "copywriter": RoleSpec("copywriter", "Copywriter", "worker", allowed_tools=[]),
    "outreach_planner": RoleSpec(
        "outreach_planner", "Outreach Planner", "worker", allowed_tools=[]
    ),
    # --- control / self-improvement plane ---
    "critic": RoleSpec("critic", "Critic / QA", "lead", allowed_tools=[]),
    "coach": RoleSpec("coach", "Coach", "lead", allowed_tools=[]),
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
