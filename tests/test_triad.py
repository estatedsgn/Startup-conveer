import json

from conveer import config, triad
from conveer.memory import Memory
from conveer.runner import RunResult
from conveer.workspace import Workspace


def test_resolve_auth_per_role(monkeypatch):
    monkeypatch.setenv("CONVEER_PROVIDER_WORKER", "api")
    monkeypatch.setenv("CONVEER_KEY_WORKER", "sk-worker")
    s = config.Settings(provider="cli")
    auth = config.resolve_auth("worker", s)
    assert auth.provider == "api" and auth.api_key == "sk-worker"
    # a role without overrides falls back to global
    assert config.resolve_auth("orchestrator", s).provider == "cli"


def test_triad_runs_brief_work_validate_fix_synthesize(tmp_path, monkeypatch):
    calls = {"worker": 0, "critic": 0}

    def fake_invoke(role, system_prompt, prompt, *, settings):
        if role == "orchestrator" and "Produce the brief" in prompt:
            return RunResult(text=json.dumps({"brief": "size the market", "success_criteria": ["TAM range"]}))
        if role == "orchestrator":  # synthesize
            return RunResult(text=json.dumps({"summary": "done", "recommendation": "iterate",
                                              "confidence": 0.7, "status": "completed"}))
        if role == "worker":
            calls["worker"] += 1
            return RunResult(text=json.dumps({"deliverable": {"n": calls["worker"]},
                                              "summary": "w", "status": "completed"}))
        if role == "critic":
            calls["critic"] += 1
            score = 0.9 if calls["critic"] >= 2 else 0.3  # fail first, pass second
            return RunResult(text=json.dumps({"score": score,
                                              "fundamental_issues": [{"principle": "cite sources"}]}))
        raise AssertionError(role)

    monkeypatch.setattr(triad, "invoke_agent", fake_invoke)
    s = config.Settings(workspace_dir=tmp_path / "ws", prompts_dir=tmp_path / "pr", factory_bar=0.75)
    ws = Workspace(s.workspace_dir)
    mem = Memory(s.workspace_dir)

    result = triad.run_triad("Validate idea X", settings=s, workspace=ws, memory=mem, max_fix=2)

    assert calls["worker"] == 2          # original + one fix
    assert calls["critic"] == 2          # failed then passed
    assert result["fix_attempts"] == 1
    assert result["validation"]["passed"] is True
    assert result["final"]["summary"] == "done"
    # A2A traffic persisted: brief, work, validate(x2), validate, synth -> several messages
    assert len(ws.conversation()) >= 5
    assert (s.workspace_dir / "agents" / "orchestrator.card.json").exists()
