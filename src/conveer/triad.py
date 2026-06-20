"""The 3-Claude triad — one contour handled by exactly three agents.

  Orchestrator (Claude #1): turns the objective into a precise brief, then
                            synthesizes the final answer.
  Worker       (Claude #2): executes the brief, fixes validator feedback.
  Validator    (Claude #3): scores the deliverable, returns fundamental issues.

Interaction: brief -> work -> validate -> (fix -> re-validate)* -> synthesize.
Each role runs on its own Claude via per-role auth (config.resolve_auth); all
messages are persisted to the shared workspace as A2A traffic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import a2a, config, prompt_store, registry, runner
from .memory import SHARED, Memory
from .observability import traceable
from .workspace import Workspace

ROLES = ("orchestrator", "worker", "critic")

_ORCH_BRIEF = """## Output (A2A)
Return ONLY a JSON object (no prose, no fences):
{"brief": "one precise task for the Worker",
 "success_criteria": ["measurable check", "..."],
 "notes": "constraints / what good looks like"}"""

_ORCH_SYNTH = """## Output (A2A)
Return ONLY a JSON object (no prose, no fences):
{"summary": "final answer for the owner",
 "result": { ... the decision-useful payload ... },
 "recommendation": "go|no-go|iterate|<action>",
 "confidence": 0.0, "status": "completed"}"""

_WORKER = """## Output (A2A)
Return ONLY a JSON object (no prose, no fences):
{"deliverable": { ... your structured work ... },
 "summary": "one line", "confidence": 0.0, "status": "completed"}"""


@traceable(name="triad_agent")
def invoke_agent(role: str, system_prompt: str, prompt: str, *, settings: config.Settings):
    """Run one triad role on ITS OWN Claude (per-role auth).

    Tools are disabled (allowed_tools=[]): triad agents reason from the model +
    memory and return structured JSON, so leaving default tools on can send a
    worker into long, unbounded tool use.
    """
    auth = config.resolve_auth(role, settings)
    # Per-role model override (e.g. worker on Qwen) wins over the Claude default.
    model = auth.model or registry.get_role(role).model(settings)
    return runner.run_agent(
        system_prompt, prompt, model=model,
        label=role, settings=settings, auth=auth, allowed_tools=[],
    )


@dataclass
class _Acc:
    cost: dict = field(default_factory=lambda: {"tokens": 0, "usd": 0.0})

    def add(self, res) -> None:
        self.cost["tokens"] = int(self.cost["tokens"]) + res.total_tokens
        self.cost["usd"] = round(float(self.cost["usd"]) + res.cost_usd, 6)


def _compose(role: str, contract: str, mem: Memory, settings: config.Settings, query: str | None = None) -> str:
    parts = [prompt_store.current_prompt(role, settings).text]
    mctx = mem.context_for(role, query=query)
    if mctx:
        parts.append(mctx)
    if contract:
        parts.append(contract)
    return "\n\n".join(parts)


def _parse(text: str) -> dict:
    try:
        return a2a.parse_agent_output(text)
    except Exception:
        return {"status": "failed", "summary": "unparseable", "raw": text[:1500]}


def run_triad(
    objective: str,
    *,
    contour: str = "idea-validation",
    settings: config.Settings | None = None,
    workspace: Workspace | None = None,
    memory: Memory | None = None,
    max_fix: int | None = None,
) -> dict:
    settings = settings or config.load_settings()
    max_fix = settings.factory_max_fix if max_fix is None else max_fix
    bar = settings.factory_bar
    ws = workspace or Workspace(settings.workspace_dir)
    mem = memory or Memory(settings.workspace_dir)
    ctx_id = a2a.new_id("ctx")
    acc = _Acc()

    for role in ROLES:
        ws.write_card(registry.agent_card(role))

    # 1) Orchestrator -> brief
    sysp = _compose("orchestrator", _ORCH_BRIEF, mem, settings, query=objective)
    r = invoke_agent("orchestrator", sysp,
                     f"CONTOUR: {contour}\nOBJECTIVE:\n{objective}\n\nProduce the brief.",
                     settings=settings)
    acc.add(r)
    brief = _parse(r.text)
    ws.post_message(a2a.data_message("agent", brief, sender="orchestrator",
                                     recipient="worker", context_id=ctx_id))

    # 2) Worker <-> Validator loop
    deliverable: dict = {}
    validation: dict = {}
    feedback: list[str] | None = None
    attempts = 0
    while True:
        wp = _compose("worker", _WORKER, mem, settings, query=brief.get("brief", objective))
        work_prompt = (
            f"BRIEF:\n{json.dumps(brief, ensure_ascii=False)}\n\nDo it."
        )
        if feedback:
            work_prompt += "\n\n## Validator feedback to fix fundamentally\n" + \
                "\n".join(f"- {f}" for f in feedback)
        rw = invoke_agent("worker", wp, work_prompt, settings=settings)
        acc.add(rw)
        deliverable = _parse(rw.text)
        ws.post_message(a2a.data_message("agent", deliverable, sender="worker",
                                         recipient="critic", task_id=None, context_id=ctx_id))

        vp = _compose("critic", "", mem, settings)
        val_prompt = (
            f"Target role: worker\nBar to pass: {bar}\n"
            f"BRIEF:\n{json.dumps(brief, ensure_ascii=False)}\n\n"
            f"DELIVERABLE:\n{json.dumps(deliverable, ensure_ascii=False)}\n\nEvaluate."
        )
        rv = invoke_agent("critic", vp, val_prompt, settings=settings)
        acc.add(rv)
        crit = _parse(rv.text)
        score = float(crit.get("score", 0.0) or 0.0)
        issues = [i.get("principle", "") for i in crit.get("fundamental_issues", [])]
        validation = {"score": score, "passed": score >= bar,
                      "issues": [i for i in issues if i], "summary": crit.get("summary", "")}
        ws.post_message(a2a.data_message("agent", validation, sender="critic",
                                         recipient="orchestrator", context_id=ctx_id))

        if validation["passed"] or attempts >= max_fix:
            break
        attempts += 1
        feedback = validation["issues"]

    # 3) Orchestrator -> synthesize
    sysp2 = _compose("orchestrator", _ORCH_SYNTH, mem, settings)
    rs = invoke_agent("orchestrator", sysp2,
                      f"OBJECTIVE:\n{objective}\n\n"
                      f"VALIDATED DELIVERABLE:\n{json.dumps(deliverable, ensure_ascii=False)}\n\n"
                      f"VALIDATION:\n{json.dumps(validation, ensure_ascii=False)}\n\nSynthesize.",
                      settings=settings)
    acc.add(rs)
    final = _parse(rs.text)
    ws.post_message(a2a.data_message("agent", final, sender="orchestrator",
                                     recipient="owner", context_id=ctx_id))
    mem.remember(f"triad on '{objective[:80]}': {final.get('summary','')}",
                 scope=SHARED, source="orchestrator", tags=["triad", contour])

    return {
        "objective": objective, "contour": contour, "context_id": ctx_id,
        "brief": brief, "deliverable": deliverable, "validation": validation,
        "fix_attempts": attempts, "final": final, "cost": acc.cost,
    }
