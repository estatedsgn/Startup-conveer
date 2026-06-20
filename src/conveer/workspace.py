"""The shared workspace — one folder where all agents live and communicate.

Layout (under WORKSPACE_DIR, on your computer):

    workspace/
      agents/<role>.card.json     # agent cards (passports)
      bus/<seq>-<from>-to-<to>.json   # every A2A message, in order (the chat log)
      tasks/<task_id>.json        # task records with status + artifacts
      artifacts/<task_id>/        # deliverables
      memory/shared.jsonl         # company-wide memory
      memory/<agent>.jsonl        # per-agent memory
      org.json                    # resolved org chart snapshot

Everything is plain JSON so you can open the folder and watch the agents talk.
"""

from __future__ import annotations

import itertools
import json
from datetime import datetime, timezone
from pathlib import Path

from . import a2a, config

_seq = itertools.count(1)


class Workspace:
    def __init__(self, root: Path | None = None):
        self.root = Path(root or config.WORKSPACE_DIR)
        self.agents = self.root / "agents"
        self.bus = self.root / "bus"
        self.tasks = self.root / "tasks"
        self.artifacts = self.root / "artifacts"
        self.memory = self.root / "memory"
        for d in (self.agents, self.bus, self.tasks, self.artifacts, self.memory):
            d.mkdir(parents=True, exist_ok=True)

    # -- agent cards -------------------------------------------------------- #
    def write_card(self, card: a2a.AgentCard) -> Path:
        path = self.agents / f"{card.name}.card.json"
        path.write_text(card.model_dump_json(indent=2), encoding="utf-8")
        return path

    # -- A2A message bus ---------------------------------------------------- #
    def post_message(self, message: a2a.Message) -> Path:
        n = next(_seq)
        frm = message.sender or "?"
        to = message.recipient or "?"
        path = self.bus / f"{n:05d}-{frm}-to-{to}.json"
        path.write_text(
            a2a.send_request(message).model_dump_json(indent=2), encoding="utf-8"
        )
        return path

    def conversation(self) -> list[dict]:
        out = []
        for p in sorted(self.bus.glob("*.json")):
            out.append(json.loads(p.read_text(encoding="utf-8")))
        return out

    # -- tasks & artifacts -------------------------------------------------- #
    def save_task(self, task: a2a.Task) -> Path:
        path = self.tasks / f"{task.id}.json"
        path.write_text(task.model_dump_json(indent=2), encoding="utf-8")
        return path

    def save_artifact(self, task_id: str, artifact: a2a.Artifact) -> Path:
        d = self.artifacts / task_id
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{artifact.artifact_id}.json"
        path.write_text(artifact.model_dump_json(indent=2), encoding="utf-8")
        return path

    def write_org(self, org: dict) -> Path:
        path = self.root / "org.json"
        path.write_text(json.dumps(org, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
