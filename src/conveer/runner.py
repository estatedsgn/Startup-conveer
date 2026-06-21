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


def build_codex_command(
    *,
    prompt: str,
    system_prompt: str = "",
    model: str | None = None,
    workdir: str | None = None,
    json_output: bool = True,
    codex_bin: str = "codex",
) -> list[str]:
    """Build the argv for a headless `codex exec` invocation.

    Codex has no `--append-system-prompt`, so the role/system prompt is prepended
    to the task in a single prompt. Each agent runs in its own ``workdir`` so its
    artifacts don't collide with the others. Kept separate for unit testing.
    """
    full_prompt = prompt
    if system_prompt:
        full_prompt = f"{system_prompt}\n\n=== TASK ===\n\n{prompt}"
    cmd = [codex_bin, "exec", "--skip-git-repo-check"]
    if json_output:
        cmd.append("--json")
    if model:
        cmd += ["--model", model]
    if workdir:
        cmd += ["-C", workdir]
    cmd.append(full_prompt)
    return cmd


def parse_codex_output(stdout: str) -> RunResult:
    """Parse `codex exec --json` output (JSONL events) into a RunResult.

    Tolerant by design: codex's event schema varies by version, so we scan every
    JSON line for the latest agent message and any token-usage event, and fall
    back to treating the whole output as plain text if it isn't JSONL.
    """
    stdout = stdout.strip()
    if not stdout:
        raise SubordinateError("Empty output from codex")

    text = ""
    in_tok = out_tok = 0
    saw_json = False
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        saw_json = True
        msg = obj.get("msg", obj) if isinstance(obj, dict) else {}
        mtype = msg.get("type")
        if mtype in {"agent_message", "assistant_message"} and msg.get("message"):
            text = msg["message"]
        elif obj.get("type") == "item.completed" and isinstance(obj.get("item"), dict):
            item = obj["item"]
            if item.get("text"):
                text = item["text"]
        elif not text and isinstance(msg.get("message"), str):
            text = msg["message"]
        # token accounting (best-effort; field names vary across versions)
        usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else msg
        if "input_tokens" in usage or "output_tokens" in usage:
            in_tok = int(usage.get("input_tokens", in_tok) or in_tok)
            out_tok = int(usage.get("output_tokens", out_tok) or out_tok)

    if not saw_json:
        # Not JSONL (e.g. --json unsupported): treat the raw output as the answer.
        return RunResult(text=stdout)
    if not text:
        raise SubordinateError("codex produced no agent message")
    return RunResult(text=text, input_tokens=in_tok, output_tokens=out_tok,
                     raw={"runtime": "codex"})


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
    auth: "config.AgentAuth | None" = None,
    workdir: str | None = None,
) -> RunResult:
    """Run an agent with an explicit (pre-composed) system prompt.

    `auth` binds this call to a specific brain (its own API key, Claude CLI login,
    or Codex CLI login), so different roles can run on different runtimes. Without
    it, the global provider/credentials from `settings` are used.
    """
    settings = settings or config.load_settings()
    provider = auth.provider if auth else settings.provider
    if auth and auth.model:
        model = auth.model
    if provider == "api":
        return _run_via_api(
            system_prompt, prompt, model=model, label=label, settings=settings, auth=auth
        )
    if provider == "codex":
        return _run_via_codex_cli(
            system_prompt, prompt, model=model, label=label,
            settings=settings, workdir=workdir, auth=auth,
        )
    return _run_via_cli(
        system_prompt, prompt, model=model, label=label,
        session_id=session_id, settings=settings, allowed_tools=allowed_tools, auth=auth,
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
    auth: "config.AgentAuth | None" = None,
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

    env = None
    if auth and auth.cli_config_dir:
        import os

        env = {**os.environ, "CLAUDE_CONFIG_DIR": auth.cli_config_dir}

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=settings.subordinate_timeout, env=env,
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


def _run_via_codex_cli(
    system_prompt: str,
    prompt: str,
    *,
    model: str | None,
    label: str,
    settings: config.Settings,
    workdir: str | None = None,
    auth: "config.AgentAuth | None" = None,
) -> RunResult:
    """Run one Codex agent through the headless `codex exec` CLI.

    Its own login is selected via CODEX_HOME (so two Codex agents are two
    distinct accounts) and it works inside its own ``workdir``.
    """
    import os

    codex_bin = shutil.which("codex") or "codex"
    cmd = build_codex_command(
        prompt=prompt, system_prompt=system_prompt,
        model=model, workdir=workdir, codex_bin=codex_bin,
    )

    env = None
    if auth and auth.codex_home:
        env = {**os.environ, "CODEX_HOME": auth.codex_home}

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=settings.subordinate_timeout, env=env,
        )
    except FileNotFoundError as exc:
        raise SubordinateError(
            "`codex` CLI not found. Install it and log in (see README)."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise SubordinateError(
            f"Agent '{label}' (codex) timed out after {settings.subordinate_timeout}s"
        ) from exc

    if proc.returncode != 0:
        raise SubordinateError(
            f"Agent '{label}' (codex) exited with code {proc.returncode}: "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )

    return parse_codex_output(proc.stdout)


def _run_via_api(
    system_prompt: str,
    prompt: str,
    *,
    model: str | None,
    label: str,
    settings: config.Settings,
    auth: "config.AgentAuth | None" = None,
) -> RunResult:
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise SubordinateError("anthropic SDK not installed (pip install anthropic).") from exc

    api_key = (auth.api_key if auth else "") or settings.api_key
    if not api_key:
        raise SubordinateError("ANTHROPIC_API_KEY not set (required for provider=api).")

    model = model or settings.worker_model
    client = anthropic.Anthropic(api_key=api_key)
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
