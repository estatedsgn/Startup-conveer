"""Compose an agent's full system prompt at runtime.

The system prompt is assembled from:
  baseline role prompt (evolvable, from prompt_store)
  + team block (who this agent may delegate to)        [orchestrators]
  + recalled memory
  + extra context
  + the A2A output contract (delegate / aggregate / worker)

This is how "everything is configured via the system prompt" — rules, team,
context and the agent-to-agent output format all live there.
"""

from __future__ import annotations

from . import config, prompt_store, registry

ORCH_DELEGATE_CONTRACT = """## How to respond (A2A — agent to agent)
You are DELEGATING. Return ONLY a JSON object (no prose, no code fences):
{
  "plan": [
    {"to": "<team member name>", "objective": "single-purpose task",
     "inputs": {}, "success_criteria": ["measurable"]}
  ],
  "rationale": "why this decomposition"
}
Delegate ONLY to members listed under "Your team"."""

ORCH_AGGREGATE_CONTRACT = """## How to respond (A2A — synthesis)
You are AGGREGATING your team's returned artifacts. Return ONLY a JSON object:
{
  "summary": "decision-useful synthesis for your manager",
  "key_findings": ["..."],
  "recommendation": "go|no-go|iterate|<concrete action>",
  "confidence": 0.0,
  "status": "completed"
}"""

WORKER_CONTRACT = """## How to respond (A2A — agent to agent)
Return ONLY a JSON object (no prose, no code fences):
{
  "artifact": { ... your role-specific structured output per your role spec above ... },
  "summary": "one line for your manager",
  "confidence": 0.0,
  "status": "completed"
}
Put your normal role output inside "artifact"."""


def build(
    role: str,
    *,
    teams: dict,
    settings: config.Settings | None = None,
    phase: str = "worker",
    memory_ctx: str = "",
    extra_context: str = "",
) -> str:
    settings = settings or config.load_settings()
    spec = registry.get_role(role)
    base = prompt_store.current_prompt(role, settings).text
    blocks: list[str] = [base]

    if spec.kind == "orchestrator":
        team = (teams.get(role) or {}).get("delegates_to", [])
        lines = [f"- {m} ({registry.get_role(m).title})" for m in team]
        blocks.append("## Your team (delegate ONLY to these)\n" + "\n".join(lines))

    if memory_ctx:
        blocks.append(memory_ctx)
    if extra_context:
        blocks.append("## Context\n" + extra_context)

    if spec.kind == "orchestrator":
        blocks.append(
            ORCH_AGGREGATE_CONTRACT if phase == "aggregate" else ORCH_DELEGATE_CONTRACT
        )
    else:
        blocks.append(WORKER_CONTRACT)

    return "\n\n".join(blocks)
