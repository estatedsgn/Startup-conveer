import json
import subprocess

import pytest

from conveer import config, runner
from conveer.runner import (
    SubordinateError,
    build_codex_command,
    build_command,
    load_role_prompt,
    parse_codex_output,
    parse_output,
    run_subordinate,
)


def test_build_command_includes_core_flags():
    cmd = build_command(
        prompt="hi",
        system_prompt="be nice",
        model="claude-x",
        session_id="sess-1",
        allowed_tools=[],
    )
    assert cmd[0:3] == ["claude", "-p", "hi"]
    assert "--output-format" in cmd and "json" in cmd
    assert "--append-system-prompt" in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-x"
    assert cmd[cmd.index("--resume") + 1] == "sess-1"
    # empty tool list => explicit empty scope
    assert cmd[cmd.index("--allowedTools") + 1] == ""


def test_parse_output_success():
    payload = {
        "is_error": False,
        "result": "hello",
        "session_id": "abc",
        "total_cost_usd": 0.012,
        "usage": {"input_tokens": 3, "output_tokens": 4},
    }
    res = parse_output(json.dumps(payload))
    assert res.text == "hello"
    assert res.session_id == "abc"
    assert res.cost_usd == 0.012
    assert res.total_tokens == 7


def test_parse_output_error_flag():
    with pytest.raises(SubordinateError):
        parse_output(json.dumps({"is_error": True, "result": "boom"}))


def test_parse_output_empty_and_bad_json():
    with pytest.raises(SubordinateError):
        parse_output("")
    with pytest.raises(SubordinateError):
        parse_output("not json")


def test_load_role_prompt_real_role():
    text = load_role_prompt("ceo")
    assert "CEO" in text


def test_run_subordinate_mocked(monkeypatch):
    class FakeProc:
        returncode = 0
        stdout = json.dumps({"result": "ok", "session_id": "s9", "usage": {}})
        stderr = ""

    def fake_run(cmd, **kwargs):
        assert "--append-system-prompt" in cmd
        return FakeProc()

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    res = run_subordinate("ceo", "decide", settings=config.load_settings())
    assert res.text == "ok"
    assert res.session_id == "s9"


def test_run_subordinate_nonzero_exit(monkeypatch):
    class FakeProc:
        returncode = 2
        stdout = ""
        stderr = "auth required"

    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: FakeProc())
    with pytest.raises(SubordinateError):
        run_subordinate("ceo", "x")


def test_run_subordinate_timeout(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=1)

    monkeypatch.setattr(runner.subprocess, "run", boom)
    with pytest.raises(SubordinateError):
        run_subordinate("ceo", "x")


def test_run_agent_via_api(monkeypatch):
    import types

    import anthropic

    class FakeMessages:
        def create(self, **kwargs):
            block = types.SimpleNamespace(text='{"ok": 1}')
            usage = types.SimpleNamespace(input_tokens=10, output_tokens=20)
            return types.SimpleNamespace(content=[block], usage=usage, stop_reason="end_turn")

    class FakeClient:
        def __init__(self, api_key=None):
            self.messages = FakeMessages()

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    s = config.Settings(provider="api", api_key="test-key")
    res = runner.run_agent("system", "hi", model="claude-opus-4-8", settings=s)
    assert res.text == '{"ok": 1}'
    assert res.input_tokens == 10 and res.output_tokens == 20
    assert res.cost_usd > 0  # priced from PRICE_PER_MTOK


def test_api_requires_key(monkeypatch):
    s = config.Settings(provider="api", api_key="")
    with pytest.raises(SubordinateError):
        runner.run_agent("system", "hi", model="claude-opus-4-8", settings=s)


# --------------------------------------------------------------------------- #
# Codex CLI backend
# --------------------------------------------------------------------------- #
def test_build_codex_command_structure():
    cmd = build_codex_command(
        prompt="do it", system_prompt="be precise",
        model="gpt-5-codex", workdir="/tmp/agent",
    )
    assert cmd[0:2] == ["codex", "exec"]
    assert "--skip-git-repo-check" in cmd and "--json" in cmd
    assert cmd[cmd.index("--model") + 1] == "gpt-5-codex"
    assert cmd[cmd.index("-C") + 1] == "/tmp/agent"
    # system prompt is prepended into the single prompt arg (codex has no system flag)
    assert cmd[-1].startswith("be precise") and "do it" in cmd[-1]


def test_parse_codex_output_jsonl():
    lines = [
        json.dumps({"msg": {"type": "agent_message", "message": "draft ready"}}),
        json.dumps({"msg": {"type": "token_count", "input_tokens": 11, "output_tokens": 5}}),
    ]
    res = parse_codex_output("\n".join(lines))
    assert res.text == "draft ready"
    assert res.input_tokens == 11 and res.output_tokens == 5


def test_parse_codex_output_plain_fallback():
    # Not JSONL => treat the whole output as the answer.
    res = parse_codex_output("just plain text answer")
    assert res.text == "just plain text answer"


def test_run_agent_via_codex(monkeypatch):
    captured = {}

    class FakeProc:
        returncode = 0
        stdout = json.dumps({"msg": {"type": "agent_message", "message": "ok-codex"}})
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        return FakeProc()

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    auth = config.AgentAuth(provider="codex", codex_home="/tmp/codex-home")
    res = runner.run_agent("sys", "task", model="gpt-5-codex",
                           settings=config.load_settings(), auth=auth)
    assert res.text == "ok-codex"
    assert captured["cmd"][0:2] == ["codex", "exec"]
    assert captured["env"]["CODEX_HOME"] == "/tmp/codex-home"
