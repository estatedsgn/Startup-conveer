"""A2A (Agent-to-Agent) message layer.

A pragmatic, local-first subset of the A2A protocol: Agent Cards, Tasks,
Messages (with Parts), and Artifacts, wrapped in JSON-RPC 2.0. Agents talk to
each other only through these structures (never free chat), and every exchange
is persisted to the shared workspace so the conversation is inspectable.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from .jsonutil import extract_json


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# --------------------------------------------------------------------------- #
# Parts, Messages, Artifacts
# --------------------------------------------------------------------------- #
class TextPart(BaseModel):
    kind: Literal["text"] = "text"
    text: str


class DataPart(BaseModel):
    kind: Literal["data"] = "data"
    data: dict[str, Any]


Part = TextPart | DataPart


class Message(BaseModel):
    role: Literal["user", "agent"]
    parts: list[Part] = Field(default_factory=list)
    message_id: str = Field(default_factory=lambda: new_id("msg"))
    task_id: str | None = None
    context_id: str | None = None
    sender: str | None = None       # agent name (a2a extension, handy locally)
    recipient: str | None = None
    timestamp: str = Field(default_factory=_now)

    def text(self) -> str:
        return "\n".join(p.text for p in self.parts if isinstance(p, TextPart))

    def data(self) -> dict[str, Any]:
        for p in self.parts:
            if isinstance(p, DataPart):
                return p.data
        return {}


class Artifact(BaseModel):
    artifact_id: str = Field(default_factory=lambda: new_id("art"))
    name: str
    parts: list[Part] = Field(default_factory=list)

    def data(self) -> dict[str, Any]:
        for p in self.parts:
            if isinstance(p, DataPart):
                return p.data
        return {}


class TaskStatus(str, Enum):
    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    FAILED = "failed"


class Task(BaseModel):
    id: str = Field(default_factory=lambda: new_id("task"))
    context_id: str = Field(default_factory=lambda: new_id("ctx"))
    objective: str = ""
    sender: str = ""        # delegating agent
    recipient: str = ""     # assigned agent
    status: TaskStatus = TaskStatus.SUBMITTED
    history: list[Message] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    created_at: str = Field(default_factory=_now)


# --------------------------------------------------------------------------- #
# Agent Card (the agent's passport — generated from the registry + team)
# --------------------------------------------------------------------------- #
class AgentCard(BaseModel):
    name: str
    title: str
    kind: Literal["orchestrator", "worker"] = "worker"
    description: str = ""
    skills: list[str] = Field(default_factory=list)
    model: str = ""
    delegates_to: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# JSON-RPC 2.0 envelope
# --------------------------------------------------------------------------- #
class JsonRpcRequest(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: str = Field(default_factory=lambda: new_id("rpc"))
    method: str = "message/send"
    params: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Builders & parsing
# --------------------------------------------------------------------------- #
def text_message(role: str, text: str, **ids: Any) -> Message:
    return Message(role=role, parts=[TextPart(text=text)], **ids)


def data_message(role: str, data: dict[str, Any], **ids: Any) -> Message:
    return Message(role=role, parts=[DataPart(data=data)], **ids)


def artifact_from_data(name: str, data: dict[str, Any]) -> Artifact:
    return Artifact(name=name, parts=[DataPart(data=data)])


def send_request(message: Message) -> JsonRpcRequest:
    return JsonRpcRequest(method="message/send", params={"message": message.model_dump()})


def parse_agent_output(text: str) -> dict[str, Any]:
    """Parse an agent's raw text into the JSON payload it was asked to return."""
    return extract_json(text, "object")
