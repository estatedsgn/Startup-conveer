import json

from conveer import config, orchestrate
from conveer.memory import Memory
from conveer.runner import RunResult
from conveer.workspace import Workspace

TEAMS = {
    "chief": {"delegates_to": ["research_lead", "copywriter"]},
    "research_lead": {"delegates_to": ["market_researcher"]},
}


def _mock_invoke(system_prompt, prompt, *, model, label, settings):
    is_delegate = "Decompose and delegate" in prompt
    is_aggregate = "Synthesize per your instructions" in prompt

    if label == "chief" and is_delegate:
        payload = {"plan": [
            {"to": "research_lead", "objective": "validate the market"},
            {"to": "copywriter", "objective": "draft the one-pager"},
        ], "rationale": "split research and messaging"}
    elif label == "research_lead" and is_delegate:
        payload = {"plan": [{"to": "market_researcher", "objective": "size the market"}],
                   "rationale": "need sizing"}
    elif is_aggregate:
        payload = {"summary": f"{label} synthesis", "key_findings": ["f1"],
                   "recommendation": "iterate", "confidence": 0.7, "status": "completed"}
    else:  # worker
        payload = {"artifact": {"role": label, "result": "did the work"},
                   "summary": f"{label} done", "confidence": 0.8, "status": "completed"}
    return RunResult(text=json.dumps(payload))


def test_run_goal_delegates_through_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrate, "invoke_agent", _mock_invoke)
    s = config.Settings(workspace_dir=tmp_path / "ws", prompts_dir=tmp_path / "pr")
    ws = Workspace(s.workspace_dir)
    mem = Memory(s.workspace_dir)

    result = orchestrate.run_goal(
        "Validate idea X end to end", root="chief", settings=s,
        teams=TEAMS, workspace=ws, memory=mem,
    )

    assert result["final"]["summary"] == "chief synthesis"
    # chief delegated to two; research_lead recursed into market_researcher
    froms = {a["from"] for a in result["artifacts"]}
    assert froms == {"research_lead", "copywriter"}
    # A2A messages were written to the shared bus
    assert len(ws.conversation()) >= 4
    # cards + org snapshot persisted
    assert (s.workspace_dir / "org.json").exists()
    assert (s.workspace_dir / "agents" / "chief.card.json").exists()
    # shared memory captured orchestrator syntheses
    assert mem.recall(scope="shared")


def test_factory_validation_fix_loop(tmp_path, monkeypatch):
    calls = {"work": 0, "val": 0}

    def fake_invoke(system_prompt, prompt, *, model, label, settings):
        if "Synthesize per your instructions" in prompt:
            return RunResult(text=json.dumps({"summary": "done", "status": "completed"}))
        if "Decompose and delegate" in prompt:
            return RunResult(text=json.dumps({"plan": [{"to": "market_researcher", "objective": "size it"}]}))
        calls["work"] += 1
        return RunResult(text=json.dumps({"artifact": {"n": calls["work"]}, "summary": "w", "status": "completed"}))

    def fake_validate(role, objective, output, ctx):
        calls["val"] += 1
        passed = calls["val"] >= 2  # fail first, pass second
        return {"score": 0.9 if passed else 0.3, "passed": passed, "issues": ["be specific"]}

    monkeypatch.setattr(orchestrate, "invoke_agent", fake_invoke)
    monkeypatch.setattr(orchestrate, "_validate", fake_validate)
    s = config.Settings(workspace_dir=tmp_path / "ws", prompts_dir=tmp_path / "pr", factory_max_fix=2)

    result = orchestrate.run_goal(
        "goal", root="chief", settings=s, verify=True,
        teams={"chief": {"delegates_to": ["market_researcher"]}},
    )
    assert calls["val"] == 2          # validated, failed, re-validated -> passed
    assert calls["work"] == 2         # original + one targeted fix
    assert result["artifacts"][0]["result"]["_validation"]["passed"] is True


def test_team_boundary_enforced(tmp_path, monkeypatch):
    # chief tries to delegate to an agent NOT on its team -> ignored
    def rogue(system_prompt, prompt, *, model, label, settings):
        if label == "chief" and "Decompose and delegate" in prompt:
            return RunResult(text=json.dumps({"plan": [{"to": "market_researcher", "objective": "x"}]}))
        if "Synthesize per your instructions" in prompt:
            return RunResult(text=json.dumps({"summary": "empty", "status": "completed"}))
        return RunResult(text=json.dumps({"artifact": {}, "status": "completed"}))

    monkeypatch.setattr(orchestrate, "invoke_agent", rogue)
    s = config.Settings(workspace_dir=tmp_path / "ws", prompts_dir=tmp_path / "pr")
    result = orchestrate.run_goal(
        "goal", root="chief", settings=s,
        teams={"chief": {"delegates_to": ["research_lead"]}},
    )
    # market_researcher was not on chief's team, so no artifacts produced
    assert result["artifacts"] == []
