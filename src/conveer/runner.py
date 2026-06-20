"""Subordinate runner: invokes a Claude "employee" via headless `claude -p`.

Each subordinate is a separate `claude` process with a role-specific system
prompt and a restricted tool scope. This is the single place that talks to the
Claude runtime, so it is also the single place to instrument (LangSmith) and to
mock in tests.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any

from . import config


class SubordinateError(RuntimeError):
    """Raised when a `claude -p` call fails or returns unparseable output."""


@dataclass
class RunResult:
    """Outcome of one subordinate invocation."""

    text: str
    session_id: str | None = None
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def load_role_prompt(role: str, settings: config.Settings | None = None) -> str:
    """Load the *current* system prompt for a role (evolved version or baseline).

    Routes through the versioned prompt store so any Coach improvements take
    effect immediately on the next invocation.
    """
    from . import prompt_store

    try:
        return prompt_store.current_prompt(role, settings).text
    except FileNotFoundError as exc:
        raise SubordinateError(str(exc)) from exc


def build_command(
    *,
    prompt: str,
    system_prompt: str,
    model: str | None = None,
    session_id: str | None = None,
    allowed_tools: list[str] | None = None,
    claude_bin: str = "claude",
) -> list[str]:
    """Build the argv for a headless `claude -p` invocation.

    Kept separate from execution so it can be asserted in tests.
    """
    cmd = [claude_bin, "-p", prompt, "--output-format", "json",
           "--append-system-prompt", system_prompt]
    if model:
        cmd += ["--model", model]
    if session_id:
        cmd += ["--resume", session_id]
    # Restrict the tool scope (least privilege). Empty list => no tools.
    if allowed_tools is not None:
        cmd += ["--allowedTools", ",".join(allowed_tools)]
    return cmd


def parse_output(stdout: str) -> RunResult:
    """Parse the JSON emitted by `claude -p --output-format json`."""
    stdout = stdout.strip()
    if not stdout:
        raise SubordinateError("Empty output from claude")
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise SubordinateError(f"Could not parse claude JSON output: {exc}") from exc

    if data.get("is_error"):
        raise SubordinateError(f"claude reported an error: {data.get('result', data)}")

    usage = data.get("usage") or {}
    return RunResult(
        text=data.get("result", ""),
        session_id=data.get("session_id"),
        cost_usd=float(data.get("total_cost_usd", 0.0) or 0.0),
        input_tokens=int(usage.get("input_tokens", 0) or 0),
        output_tokens=int(usage.get("output_tokens", 0) or 0),
        raw=data,
    )


def run_subordinate(
    role: str,
    prompt: str,
    *,
    session_id: str | None = None,
    settings: config.Settings | None = None,
    allowed_tools: list[str] | None = None,
) -> RunResult:
    """Run one subordinate (a Claude employee) and return its parsed result.

    `role` selects both the system prompt (roles/<role>.md) and the model tier.
    """
    settings = settings or config.load_settings()
    claude_bin = shutil.which("claude") or "claude"
    system_prompt = load_role_prompt(role, settings)
    cmd = build_command(
        prompt=prompt,
        system_prompt=system_prompt,
        model=settings.model_for(role),
        session_id=session_id,
        allowed_tools=allowed_tools,
        claude_bin=claude_bin,
    )

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=settings.subordinate_timeout,
        )
    except FileNotFoundError as exc:
        raise SubordinateError(
            "`claude` CLI not found. Install it and log in (see README)."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise SubordinateError(
            f"Subordinate '{role}' timed out after {settings.subordinate_timeout}s"
        ) from exc

    if proc.returncode != 0:
        raise SubordinateError(
            f"Subordinate '{role}' exited with code {proc.returncode}: "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )

    return parse_output(proc.stdout)
