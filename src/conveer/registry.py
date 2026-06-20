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
    # --- phase 1: venture studio (planning a green-lit idea into execution) ---
    "market_researcher": RoleSpec(
        "market_researcher", "Market Researcher", "lead", allowed_tools=[]
    ),
    "product_manager": RoleSpec(
        "product_manager", "Product Manager", "lead", allowed_tools=[]
    ),
    "tech_lead": RoleSpec("tech_lead", "Tech Lead", "lead", allowed_tools=[]),
    "growth_marketer": RoleSpec(
        "growth_marketer", "Growth Marketer", "worker", allowed_tools=[]
    ),
    "finance": RoleSpec("finance", "Finance Lead (CFO)", "lead", allowed_tools=[]),
    "coo": RoleSpec("coo", "COO", "lead", allowed_tools=[]),
    # --- the owner's hands-on operator (tells the human exactly what to do) ---
    # Gets web research so its money plays are grounded in the real market.
    "operator": RoleSpec(
        "operator", "Operator", "lead", allowed_tools=["WebSearch", "WebFetch"]
    ),
}


def get_role(name: str) -> RoleSpec:
    if name not in REGISTRY:
        raise KeyError(f"Unknown role '{name}'. Known: {sorted(REGISTRY)}")
    return REGISTRY[name]
