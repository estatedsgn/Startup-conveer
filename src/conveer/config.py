"""Configuration: model tiers per role, paths, runtime tuning.

Reads from environment (.env loaded by the CLI). Keeps sensible defaults so the
package is importable and testable without any env set.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# --- paths ---
PACKAGE_DIR = Path(__file__).resolve().parent
ROLES_DIR = PACKAGE_DIR / "roles"
# Project root is two levels up from src/conveer/. Falls back to cwd if installed.
_root = PACKAGE_DIR.parent.parent
PROJECT_ROOT = _root if (_root / "pyproject.toml").exists() else Path.cwd()
RUNS_DIR = PROJECT_ROOT / "runs"
CHECKPOINT_DB = PROJECT_ROOT / ".conveer" / "checkpoints.sqlite"
# Evolving (versioned) prompts live here; baseline seeds come from ROLES_DIR.
PROMPTS_DIR = PROJECT_ROOT / "prompts"
# The ONE shared folder where all agents live and communicate (A2A bus + memory).
# Override with CONVEER_WORKSPACE to point at any folder on your computer.
WORKSPACE_DIR = Path(os.getenv("CONVEER_WORKSPACE", str(PROJECT_ROOT / "workspace")))
# Team / org chart manifest (who delegates to whom).
TEAMS_FILE = Path(os.getenv("CONVEER_TEAMS", str(PROJECT_ROOT / "teams.yaml")))

# --- model tiers ---
# Lead tier = reasoning-heavy roles (CEO, Analyst, control plane). Worker tier = cheaper.
LEAD_MODEL = os.getenv("CONVEER_MODEL_LEAD", "claude-opus-4-8")
WORKER_MODEL = os.getenv("CONVEER_MODEL_WORKER", "claude-sonnet-4-6")
# Codex tier = OpenAI coding model, driven through the `codex` CLI (provider=codex).
CODEX_MODEL = os.getenv("CONVEER_MODEL_CODEX", "gpt-5-codex")

# Map each role to a model. Keeping it explicit makes cost obvious.
ROLE_MODELS: dict[str, str] = {
    # department #1
    "ceo": LEAD_MODEL,
    "analyst": LEAD_MODEL,
    "idea_generator": WORKER_MODEL,
    "reporter": WORKER_MODEL,
    # expanded roster (workers)
    "market_researcher": WORKER_MODEL,
    "competitor_analyst": WORKER_MODEL,
    "copywriter": WORKER_MODEL,
    "outreach_planner": WORKER_MODEL,
    # control / self-improvement plane (reasoning-heavy)
    "critic": LEAD_MODEL,
    "coach": LEAD_MODEL,
    # generic 3-agent triad
    "orchestrator": LEAD_MODEL,
    "worker": WORKER_MODEL,
    # marketing / PR department (2 Claude "think & write" + 2 Codex "build & automate")
    "cmo": LEAD_MODEL,
    "smm_copywriter": WORKER_MODEL,
    "lead_scout": CODEX_MODEL,
    "outreach_operator": CODEX_MODEL,
}

# --- runtime tuning ---
SUBORDINATE_TIMEOUT = int(os.getenv("CONVEER_SUBORDINATE_TIMEOUT", "300"))
RECURSION_LIMIT = int(os.getenv("CONVEER_RECURSION_LIMIT", "25"))
# Runtime backend for agents: "cli" (headless claude, your subscription) or
# "api" (Anthropic API, built for parallel automation).
PROVIDER = os.getenv("CONVEER_PROVIDER", "cli")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
API_MAX_TOKENS = int(os.getenv("CONVEER_API_MAX_TOKENS", "4096"))
# Approx USD per 1M tokens (input, output) for cost accounting in API mode.
PRICE_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}
# Self-improvement loop defaults.
IMPROVE_BAR = float(os.getenv("CONVEER_IMPROVE_BAR", "0.8"))
IMPROVE_MAX_ROUNDS = int(os.getenv("CONVEER_IMPROVE_MAX_ROUNDS", "3"))
# Factory validation loop (validator -> targeted fix -> re-validate).
FACTORY_BAR = float(os.getenv("CONVEER_FACTORY_BAR", "0.75"))
FACTORY_MAX_FIX = int(os.getenv("CONVEER_FACTORY_MAX_FIX", "2"))


@dataclass
class Settings:
    """Resolved runtime settings (snapshot of the module-level config)."""

    lead_model: str = LEAD_MODEL
    worker_model: str = WORKER_MODEL
    role_models: dict[str, str] = field(default_factory=lambda: dict(ROLE_MODELS))
    subordinate_timeout: int = SUBORDINATE_TIMEOUT
    recursion_limit: int = RECURSION_LIMIT
    roles_dir: Path = ROLES_DIR
    runs_dir: Path = RUNS_DIR
    checkpoint_db: Path = CHECKPOINT_DB
    prompts_dir: Path = PROMPTS_DIR
    workspace_dir: Path = WORKSPACE_DIR
    teams_file: Path = TEAMS_FILE
    improve_bar: float = IMPROVE_BAR
    improve_max_rounds: int = IMPROVE_MAX_ROUNDS
    max_delegation_depth: int = int(os.getenv("CONVEER_MAX_DEPTH", "3"))
    provider: str = PROVIDER
    api_key: str = ANTHROPIC_API_KEY
    api_max_tokens: int = API_MAX_TOKENS
    factory_bar: float = FACTORY_BAR
    factory_max_fix: int = FACTORY_MAX_FIX

    def model_for(self, role: str) -> str:
        return self.role_models.get(role, self.worker_model)


def load_settings() -> Settings:
    """Build a Settings snapshot from the current environment."""
    return Settings()


@dataclass
class AgentAuth:
    """Which runtime/account a given role talks to (its own subscription/key).

    A role can be a distinct Claude (cli login or API key) *or* a Codex agent
    (separate `codex` CLI login), so a team can mix providers.
    """

    provider: str            # "cli" (Claude CLI) | "api" (Anthropic API) | "codex" (Codex CLI)
    api_key: str = ""        # for provider=api
    cli_config_dir: str | None = None  # for provider=cli (separate logged-in account)
    codex_home: str | None = None      # for provider=codex (CODEX_HOME, separate login)
    model: str | None = None           # optional per-agent model override


def resolve_auth(role: str, settings: Settings | None = None) -> AgentAuth:
    """Resolve per-role credentials so each agent can be a distinct brain.

    Env overrides (role upper-cased), falling back to the global config:
      CONVEER_PROVIDER_<ROLE>   cli|api|codex for this role
      CONVEER_KEY_<ROLE>        Anthropic API key for this role (provider=api)
      CONVEER_CLAUDE_DIR_<ROLE> CLAUDE_CONFIG_DIR for this role (separate Claude CLI login)
      CONVEER_CODEX_HOME_<ROLE> CODEX_HOME for this role (separate Codex CLI login)
      CONVEER_MODEL_<ROLE>      model id override for this role
    """
    settings = settings or load_settings()
    r = role.upper()
    provider = os.getenv(f"CONVEER_PROVIDER_{r}", settings.provider)
    api_key = os.getenv(f"CONVEER_KEY_{r}", "") or (settings.api_key if provider == "api" else "")
    cli_dir = os.getenv(f"CONVEER_CLAUDE_DIR_{r}")
    codex_home = os.getenv(f"CONVEER_CODEX_HOME_{r}")
    model = os.getenv(f"CONVEER_MODEL_{r}")
    return AgentAuth(
        provider=provider, api_key=api_key, cli_config_dir=cli_dir,
        codex_home=codex_home, model=model,
    )


def agent_workdir(role: str, settings: Settings | None = None) -> Path:
    """Each agent's own working area inside the shared workspace.

    Codex agents run with this as their CWD (`codex exec -C <dir>`), so each one
    builds artifacts in its own folder without stepping on the others.
    """
    settings = settings or load_settings()
    d = settings.workspace_dir / "agents" / role
    d.mkdir(parents=True, exist_ok=True)
    return d
