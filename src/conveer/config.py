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

# --- model tiers ---
# Lead tier = reasoning-heavy roles (CEO, Analyst). Worker tier = cheaper roles.
LEAD_MODEL = os.getenv("CONVEER_MODEL_LEAD", "claude-opus-4-8")
WORKER_MODEL = os.getenv("CONVEER_MODEL_WORKER", "claude-sonnet-4-6")

# Map each role to a model. Keeping it explicit makes cost obvious.
ROLE_MODELS: dict[str, str] = {
    # department #1
    "ceo": LEAD_MODEL,
    "analyst": LEAD_MODEL,
    "idea_generator": WORKER_MODEL,
    "reporter": WORKER_MODEL,
    # phase 1: venture studio
    "market_researcher": LEAD_MODEL,
    "product_manager": LEAD_MODEL,
    "tech_lead": LEAD_MODEL,
    "finance": LEAD_MODEL,
    "coo": LEAD_MODEL,
    "operator": LEAD_MODEL,
    "growth_marketer": WORKER_MODEL,
}

# --- runtime tuning ---
SUBORDINATE_TIMEOUT = int(os.getenv("CONVEER_SUBORDINATE_TIMEOUT", "300"))
RECURSION_LIMIT = int(os.getenv("CONVEER_RECURSION_LIMIT", "25"))


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

    def model_for(self, role: str) -> str:
        return self.role_models.get(role, self.worker_model)


def load_settings() -> Settings:
    """Build a Settings snapshot from the current environment."""
    return Settings()
