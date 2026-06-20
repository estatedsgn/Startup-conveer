"""Delegation runtime: orchestrators decompose goals, delegate to their team,
verify, and synthesize — all communicating in A2A format through the shared
workspace folder.

Tree: chief -> {research_lead, gtm_lead} -> workers. An orchestrator delegates
to its team (recursing into sub-orchestrators), collects each returned Artifact
as an A2A message on the bus, then aggregates a synthesis for its manager.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import a2a, config, org, prompt_builder, registry, runner
from .memory import SHARED, Memory
from .observability import traceable
from .workspace import Workspace


@traceable(name="a2a_agent")
def invoke_agent(
    system_prompt: str, prompt: str, *, model: str, label: str, settings: config.Settings
) -> runner.RunResult:
    return runner.run_agent(system_prompt, prompt, model=model, label=label, settings=settings)


@dataclass
class _Ctx:
    settings: config.Settings
    teams: dict
    ws: Workspace
    mem: Memory
    verify: bool
    context_id: str
    cost: dict[str, float] = field(default_factory=lambda: {"tokens": 0, "usd": 0.0})

    def add(self, res: runner.RunResult) -> None:
        self.cost["tokens"] = int(self.cost["tokens"]) + res.total_tokens
        self.cost["usd"] = round(float(self.cost["usd"]) + res.cost_usd, 6)


def _delegate_prompt(objective: str) -> str:
    return f"GOAL TO ACHIEVE:\n{objective}\n\nDecompose and delegate per your instructions."


def _aggregate_prompt(objective: str, artifacts: list[dict]) -> str:
    import json

    return (
        f"GOAL:\n{objective}\n\n"
        f"YOUR TEAM'S RETURNED ARTIFACTS:\n{json.dumps(artifacts, ensure_ascii=False, indent=2)}\n\n"
        "Synthesize per your instructions."
    )


def _work_prompt(objective: str) -> str:
    return f"TASK:\n{objective}\n\nDo it and respond per your instructions."


def _safe_parse(text: str) -> dict[str, Any]:
    try:
        return a2a.parse_agent_output(text)
    except Exception:
        return {"status": "failed", "summary": "unparseable output", "raw": text[:2000]}


def _validate(role: str, objective: str, output: dict, ctx: _Ctx) -> dict:
    """Validator (Critic) judges a worker's output and surfaces issues to fix."""
    import json

    prompt = (
        f"Target role: {role}\nBar to pass: {ctx.settings.factory_bar}\n"
        f"TASK: {objective}\n\nOUTPUT:\n{json.dumps(output, ensure_ascii=False)}\n\n"
        "Evaluate per your instructions."
    )
    try:
        res = runner.run_subordinate("critic", prompt, settings=ctx.settings)
        ctx.add(res)
        crit = _safe_parse(res.text)
        score = float(crit.get("score", 0.0) or 0.0)
        issues = [i.get("principle", "") for i in crit.get("fundamental_issues", [])]
        return {
            "score": score,
            "passed": score >= ctx.settings.factory_bar,
            "issues": [i for i in issues if i],
            "summary": crit.get("summary", ""),
        }
    except Exception as exc:  # validation is best-effort
        return {"score": None, "passed": True, "issues": [], "error": str(exc)}


def _run_worker_once(
    role: str, objective: str, ctx: _Ctx, manager: str, feedback: list[str] | None
) -> dict:
    spec = registry.get_role(role)
    mem_ctx = ctx.mem.context_for(role, query=objective)
    extra = f"Task from {manager}: {objective}"
    if feedback:
        extra += "\n\n## Validator feedback to address (fix these fundamentally)\n" + \
            "\n".join(f"- {f}" for f in feedback)
    sysp = prompt_builder.build(
        role, teams=ctx.teams, settings=ctx.settings, phase="worker",
        memory_ctx=mem_ctx, extra_context=extra,
    )
    res = invoke_agent(
        sysp, _work_prompt(objective), model=spec.model(ctx.settings),
        label=role, settings=ctx.settings,
    )
    ctx.add(res)
    return _safe_parse(res.text)


def _work(role: str, objective: str, ctx: _Ctx, manager: str) -> dict:
    """Factory worker path: produce -> validate -> targeted fix -> re-validate."""
    out = _run_worker_once(role, objective, ctx, manager, feedback=None)

    if ctx.verify:
        attempts = 0
        while True:
            verdict = _validate(role, objective, out, ctx)
            out["_validation"] = verdict
            if verdict.get("passed") or attempts >= ctx.settings.factory_max_fix:
                break
            attempts += 1
            out = _run_worker_once(role, objective, ctx, manager, feedback=verdict.get("issues"))

    ctx.mem.remember(
        f"{role} did '{objective[:80]}': {out.get('summary','')}",
        scope=role, source=role, tags=[role],
    )
    return out


def _delegate(role: str, objective: str, depth: int, ctx: _Ctx, manager: str) -> dict:
    spec = registry.get_role(role)
    mem_ctx = ctx.mem.context_for(role, query=objective)

    # 1) decompose & delegate
    sysp = prompt_builder.build(
        role, teams=ctx.teams, settings=ctx.settings, phase="delegate",
        memory_ctx=mem_ctx, extra_context=f"Goal from {manager}: {objective}",
    )
    res = invoke_agent(
        sysp, _delegate_prompt(objective), model=spec.model(ctx.settings),
        label=role, settings=ctx.settings,
    )
    ctx.add(res)
    plan = _safe_parse(res.text).get("plan", []) or []
    allowed = set(org.delegates_of(role, ctx.teams))

    artifacts: list[dict] = []
    for sub in plan:
        to = sub.get("to")
        if to not in allowed:
            continue  # enforce team boundary (security: no out-of-team delegation)
        sub_obj = sub.get("objective", "")
        task = a2a.Task(
            context_id=ctx.context_id, objective=sub_obj, sender=role,
            recipient=to, status=a2a.TaskStatus.WORKING,
        )
        ctx.ws.post_message(a2a.data_message(
            "agent",
            {"objective": sub_obj, "inputs": sub.get("inputs", {}),
             "success_criteria": sub.get("success_criteria", [])},
            sender=role, recipient=to, task_id=task.id, context_id=ctx.context_id,
        ))
        result = _execute(to, sub_obj, depth + 1, ctx, manager=role)
        art = a2a.artifact_from_data(f"{to}-artifact", result)
        ctx.ws.save_artifact(task.id, art)
        task.status = a2a.TaskStatus.COMPLETED
        task.artifacts = [art]
        ctx.ws.save_task(task)
        ctx.ws.post_message(a2a.data_message(
            "agent", result, sender=to, recipient=role,
            task_id=task.id, context_id=ctx.context_id,
        ))
        artifacts.append({"from": to, "objective": sub_obj, "result": result})

    # 2) aggregate / synthesize
    sysp2 = prompt_builder.build(
        role, teams=ctx.teams, settings=ctx.settings, phase="aggregate", memory_ctx=mem_ctx,
    )
    res2 = invoke_agent(
        sysp2, _aggregate_prompt(objective, artifacts), model=spec.model(ctx.settings),
        label=role, settings=ctx.settings,
    )
    ctx.add(res2)
    synthesis = _safe_parse(res2.text)
    ctx.mem.remember(
        f"{role} on '{objective[:80]}': {synthesis.get('summary','')}",
        scope=SHARED, source=role, tags=[role],
    )
    return {"final": synthesis, "artifacts": artifacts}


def _execute(role: str, objective: str, depth: int, ctx: _Ctx, manager: str) -> dict:
    if registry.is_orchestrator(role) and depth < ctx.settings.max_delegation_depth:
        return _delegate(role, objective, depth, ctx, manager)
    return _work(role, objective, ctx, manager)


def run_goal(
    goal: str,
    *,
    root: str = "chief",
    settings: config.Settings | None = None,
    teams: dict | None = None,
    workspace: Workspace | None = None,
    memory: Memory | None = None,
    verify: bool = False,
    context_id: str | None = None,
) -> dict:
    """Run a goal through the agent tree. Returns the root's synthesis + cost."""
    settings = settings or config.load_settings()
    teams = teams if teams is not None else org.load_teams(settings.teams_file)
    ws = workspace or Workspace(settings.workspace_dir)
    mem = memory or Memory(settings.workspace_dir)
    ctx = _Ctx(settings, teams, ws, mem, verify, context_id or a2a.new_id("ctx"))

    # Publish agent cards + org snapshot into the shared folder.
    for r in sorted(org.subtree_roles(teams, root)):
        ws.write_card(registry.agent_card(r, org.delegates_of(r, teams)))
    ws.write_org(org.tree(teams, root))

    result = _execute(root, goal, depth=0, ctx=ctx, manager="owner")
    final = result.get("final", result)
    return {
        "goal": goal, "root": root, "context_id": ctx.context_id,
        "final": final, "artifacts": result.get("artifacts", []), "cost": ctx.cost,
    }
