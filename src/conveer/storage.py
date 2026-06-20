"""Artifact storage: persists each run's outputs under runs/<run_id>/."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("run-%Y%m%d-%H%M%S")


def run_dir(run_id: str, runs_dir: Path | None = None) -> Path:
    base = runs_dir or config.RUNS_DIR
    d = base / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_json(run_id: str, name: str, data: Any, runs_dir: Path | None = None) -> Path:
    path = run_dir(run_id, runs_dir) / name
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def save_text(run_id: str, name: str, text: str, runs_dir: Path | None = None) -> Path:
    path = run_dir(run_id, runs_dir) / name
    path.write_text(text, encoding="utf-8")
    return path
