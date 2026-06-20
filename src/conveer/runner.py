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


def run_agent(
    system_prompt: str,
    prompt: str,
    *,
    model: str | None = None,
    label: str = "agent",
    session_id: str | None = None,
    settings: config.Settings | None = None,
    allowed_tools: list[str] | None = None,
) -> RunResult:
    """Run a Claude agent with an explicit (pre-composed) system prompt.

    Dispatches to the configured provider: "cli" (headless `claude`, your
    subscription) or "api" (Anthropic API, for parallel automation). `label` is
    only used in error messages.
    """
    settings = settings or config.load_settings()
    if settings.provider == "api":
        return _run_via_api(system_prompt, prompt, model=model, label=label, settings=settings)
    return _run_via_cli(
        system_prompt, prompt, model=model, label=label,
        session_id=session_id, settings=settings, allowed_tools=allowed_tools,
    )


def _run_via_cli(
    system_prompt: str,
    prompt: str,
    *,
    model: str | None,
    label: str,
    session_id: str | None,
    settings: config.Settings,
    allowed_tools: list[str] | None,
) -> RunResult:
    claude_bin = shutil.which("claude") or "claude"
    cmd = build_command(
        prompt=prompt,
        system_prompt=system_prompt,
        model=model,
        session_id=session_id,
        allowed_tools=allowed_tools,
        claude_bin=claude_bin,
    )

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=settings.subordinate_timeout
        )
    except FileNotFoundError as exc:
        raise SubordinateError(
            "`claude` CLI not found. Install it and log in (see README)."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise SubordinateError(
            f"Agent '{label}' timed out after {settings.subordinate_timeout}s"
        ) from exc

    if proc.returncode != 0:
        raise SubordinateError(
            f"Agent '{label}' exited with code {proc.returncode}: "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )

    return parse_output(proc.stdout)


def _run_via_api(
    system_prompt: str,
    prompt: str,
    *,
    model: str | None,
    label: str,
    settings: config.Settings,
) -> RunResult:
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise SubordinateError("anthropic SDK not installed (pip install anthropic).") from exc

    if not settings.api_key:
        raise SubordinateError("ANTHROPIC_API_KEY not set (required for provider=api).")

    model = model or settings.worker_model
    client = anthropic.Anthropic(api_key=settings.api_key)
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=settings.api_max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:  # surface API errors uniformly
        raise SubordinateError(f"Agent '{label}' API call failed: {exc}") from exc

    text = "".join(getattr(b, "text", "") for b in resp.content)
    usage = resp.usage
    in_tok = int(getattr(usage, "input_tokens", 0) or 0)
    out_tok = int(getattr(usage, "output_tokens", 0) or 0)
    pin, pout = config.PRICE_PER_MTOK.get(model, (0.0, 0.0))
    cost = round(in_tok / 1_000_000 * pin + out_tok / 1_000_000 * pout, 6)
    return RunResult(
        text=text, session_id=None, cost_usd=cost,
        input_tokens=in_tok, output_tokens=out_tok,
        raw={"model": model, "stop_reason": resp.stop_reason},
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
    system_prompt = load_role_prompt(role, settings)
    return run_agent(
        system_prompt,
        prompt,
        model=settings.model_for(role),
        label=role,
        session_id=session_id,
        settings=settings,
        allowed_tools=allowed_tools,
    )
