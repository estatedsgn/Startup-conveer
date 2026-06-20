"""Versioned prompt store — lets each agent *develop* over time.

Baseline prompts ship in the package (``roles/<role>.md``, treated as v0).
When the Coach improves a role fundamentally, the new system prompt is saved as
``prompts/<role>/v{n}.md`` with a manifest tracking versions, scores and the
rationale for each change. The runner always loads the current version, so an
improvement immediately takes effect — and any version can be rolled back to.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import config


@dataclass
class PromptVersion:
    role: str
    version: int          # 0 == packaged baseline
    text: str
    score: float | None = None
    rationale: str = ""
    created: str = ""

    @property
    def label(self) -> str:
        return "v0-baseline" if self.version == 0 else f"v{self.version}"


def _role_dir(role: str, store: Path | None = None) -> Path:
    return (store or config.PROMPTS_DIR) / role


def _manifest_path(role: str, store: Path | None = None) -> Path:
    return _role_dir(role, store) / "manifest.json"


def _read_manifest(role: str, store: Path | None = None) -> dict:
    path = _manifest_path(role, store)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"role": role, "current": 0, "versions": []}


def _baseline_text(role: str, settings: config.Settings | None = None) -> str:
    settings = settings or config.load_settings()
    path = settings.roles_dir / f"{role}.md"
    if not path.exists():
        raise FileNotFoundError(f"No baseline prompt for role '{role}' at {path}")
    return path.read_text(encoding="utf-8")


def current_prompt(role: str, settings: config.Settings | None = None) -> PromptVersion:
    """Return the active prompt for a role (evolved version, else baseline)."""
    settings = settings or config.load_settings()
    store = settings.prompts_dir
    manifest = _read_manifest(role, store)
    cur = int(manifest.get("current", 0))
    if cur >= 1:
        vfile = _role_dir(role, store) / f"v{cur}.md"
        if vfile.exists():
            meta = next(
                (v for v in manifest.get("versions", []) if v.get("version") == cur), {}
            )
            return PromptVersion(
                role=role, version=cur, text=vfile.read_text(encoding="utf-8"),
                score=meta.get("score"), rationale=meta.get("rationale", ""),
                created=meta.get("created", ""),
            )
    # Fall back to the packaged baseline.
    return PromptVersion(role=role, version=0, text=_baseline_text(role, settings))


def _next_version(role: str, store: Path) -> int:
    existing = [
        int(m.group(1))
        for p in _role_dir(role, store).glob("v*.md")
        if (m := re.match(r"v(\d+)\.md$", p.name))
    ]
    return (max(existing) + 1) if existing else 1


def save_version(
    role: str,
    text: str,
    *,
    score: float | None = None,
    rationale: str = "",
    settings: config.Settings | None = None,
) -> PromptVersion:
    """Persist a new evolved prompt version and make it current."""
    settings = settings or config.load_settings()
    store = settings.prompts_dir
    rdir = _role_dir(role, store)
    rdir.mkdir(parents=True, exist_ok=True)
    version = _next_version(role, store)
    (rdir / f"v{version}.md").write_text(text, encoding="utf-8")

    manifest = _read_manifest(role, store)
    manifest["role"] = role
    manifest["current"] = version
    manifest.setdefault("versions", []).append(
        {
            "version": version,
            "score": score,
            "rationale": rationale,
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    )
    _manifest_path(role, store).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return PromptVersion(role, version, text, score, rationale)


def set_current(role: str, version: int, settings: config.Settings | None = None) -> None:
    """Roll back / forward to a specific version (0 = baseline)."""
    settings = settings or config.load_settings()
    manifest = _read_manifest(role, settings.prompts_dir)
    manifest["current"] = int(version)
    path = _manifest_path(role, settings.prompts_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def history(role: str, settings: config.Settings | None = None) -> list[dict]:
    settings = settings or config.load_settings()
    return _read_manifest(role, settings.prompts_dir).get("versions", [])
