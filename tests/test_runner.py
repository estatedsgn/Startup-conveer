import json
import subprocess

import pytest

from conveer import config, runner
from conveer.runner import (
    SubordinateError,
    build_command,
    load_role_prompt,
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
