"""Marketing/PR department wiring: 4 agents, mixed providers, per-role auth."""

from pathlib import Path

from conveer import config, org, orchestrate, prompt_builder, registry, runner

TEAMS_FILE = Path(__file__).resolve().parent.parent / "teams.marketing.yaml"

MARKETING_ROLES = {"cmo", "smm_copywriter", "lead_scout", "outreach_operator"}


def test_marketing_roles_registered():
    for role in MARKETING_ROLES:
        assert role in registry.REGISTRY
    assert registry.is_orchestrator("cmo")
    # the two Codex builders are plain workers
    assert not registry.is_orchestrator("lead_scout")
    assert not registry.is_orchestrator("outreach_operator")


def test_marketing_team_tree():
    teams = org.load_teams(TEAMS_FILE)
    assert org.subtree_roles(teams, "cmo") == MARKETING_ROLES
    assert set(org.delegates_of("cmo", teams)) == MARKETING_ROLES - {"cmo"}


def test_codex_roles_use_codex_model():
    s = config.load_settings()
    assert s.model_for("lead_scout") == config.CODEX_MODEL
    assert s.model_for("outreach_operator") == config.CODEX_MODEL
    assert s.model_for("cmo") == config.LEAD_MODEL


def test_resolve_auth_codex_from_env(monkeypatch):
    monkeypatch.setenv("CONVEER_PROVIDER_LEAD_SCOUT", "codex")
    monkeypatch.setenv("CONVEER_CODEX_HOME_LEAD_SCOUT", "/tmp/codex-scout")
    auth = config.resolve_auth("lead_scout")
    assert auth.provider == "codex"
    assert auth.codex_home == "/tmp/codex-scout"


def test_invoke_agent_threads_per_role_auth(monkeypatch):
    """Each delegated role must run on its own provider + working folder."""
    monkeypatch.setenv("CONVEER_PROVIDER_LEAD_SCOUT", "codex")

    captured = {}

    def fake_run_agent(system_prompt, prompt, **kwargs):
        captured["auth"] = kwargs.get("auth")
        captured["workdir"] = kwargs.get("workdir")
        return runner.RunResult(text="{}")

    monkeypatch.setattr(orchestrate.runner, "run_agent", fake_run_agent)
    orchestrate.invoke_agent(
        "sys", "task", model="gpt-5-codex", label="lead_scout",
        settings=config.load_settings(),
    )
    assert captured["auth"].provider == "codex"
    assert captured["workdir"].endswith("agents/lead_scout")


def test_prompt_builder_composes_marketing_roles():
    teams = org.load_teams(TEAMS_FILE)
    # orchestrator gets a team block + delegate contract
    cmo = prompt_builder.build("cmo", teams=teams, phase="delegate")
    assert "Your team" in cmo and "smm_copywriter" in cmo
    # worker gets the worker A2A contract
    scout = prompt_builder.build("lead_scout", teams=teams, phase="worker")
    assert "artifact" in scout
