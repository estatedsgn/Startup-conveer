"""Org chart loading (teams.yaml) and tree helpers."""

from __future__ import annotations

from pathlib import Path

import yaml

from . import config


def load_teams(path: Path | None = None) -> dict:
    path = Path(path or config.TEAMS_FILE)
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def delegates_of(role: str, teams: dict) -> list[str]:
    return list((teams.get(role) or {}).get("delegates_to", []))


def tree(teams: dict, root: str = "chief", _seen: set | None = None) -> dict:
    _seen = _seen or set()
    if root in _seen:  # guard against cycles
        return {"role": root, "children": []}
    _seen.add(root)
    return {
        "role": root,
        "children": [tree(teams, c, _seen) for c in delegates_of(root, teams)],
    }


def subtree_roles(teams: dict, root: str = "chief") -> set[str]:
    roles: set[str] = set()

    def walk(r: str) -> None:
        if r in roles:
            return
        roles.add(r)
        for c in delegates_of(r, teams):
            walk(c)

    walk(root)
    return roles


def render_tree(node: dict, indent: int = 0) -> str:
    lines = ["  " * indent + f"- {node['role']}"]
    for child in node.get("children", []):
        lines.append(render_tree(child, indent + 1))
    return "\n".join(lines)
