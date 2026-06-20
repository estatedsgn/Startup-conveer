"""File-based memory for the agent company — local-first, no external service.

Two scopes:
- ``shared``  — company-wide knowledge every agent can draw on.
- ``<agent>`` — an individual agent's accumulated notes.

Each entry is one JSON line in ``workspace/memory/<scope>.jsonl``. Retrieval is
deliberately simple (tag/substring filter + recency); it can be swapped for a
vector/Mem0 backend later without changing callers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import config

SHARED = "shared"


@dataclass
class MemoryEntry:
    text: str
    scope: str = SHARED
    tags: list[str] | None = None
    source: str = ""
    created: str = ""

    def to_json(self) -> dict:
        return {
            "text": self.text,
            "scope": self.scope,
            "tags": self.tags or [],
            "source": self.source,
            "created": self.created or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }


class Memory:
    def __init__(self, root: Path | None = None):
        self.dir = Path(root or config.WORKSPACE_DIR) / "memory"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, scope: str) -> Path:
        return self.dir / f"{scope}.jsonl"

    def remember(
        self, text: str, *, scope: str = SHARED, tags: list[str] | None = None, source: str = ""
    ) -> MemoryEntry:
        entry = MemoryEntry(text=text, scope=scope, tags=tags, source=source)
        with self._path(scope).open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry.to_json(), ensure_ascii=False) + "\n")
        return entry

    def _read(self, scope: str) -> list[dict]:
        path = self._path(scope)
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def recall(
        self, *, scope: str = SHARED, query: str | None = None, tags: list[str] | None = None, limit: int = 5
    ) -> list[dict]:
        entries = self._read(scope)
        if query:
            q = query.lower()
            entries = [e for e in entries if q in e.get("text", "").lower()]
        if tags:
            tagset = set(tags)
            entries = [e for e in entries if tagset & set(e.get("tags", []))]
        return entries[-limit:]

    def context_for(self, agent: str, *, query: str | None = None, limit: int = 5) -> str:
        """Build a compact memory block to inject into an agent's prompt."""
        shared = self.recall(scope=SHARED, query=query, limit=limit)
        mine = self.recall(scope=agent, query=query, limit=limit)
        if not shared and not mine:
            return ""
        lines = ["# Memory (recall)"]
        for e in shared:
            lines.append(f"- [company] {e['text']}")
        for e in mine:
            lines.append(f"- [you] {e['text']}")
        return "\n".join(lines)
