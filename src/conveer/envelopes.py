"""Interaction contracts between agents.

Every handoff in the company travels as a typed envelope, never as free chat.
See docs/protocol.md for the regulation these models enforce.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Priority(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"


class ResultStatus(str, Enum):
    DONE = "done"
    BLOCKED = "blocked"
    NEEDS_HUMAN = "needs_human"


class Recommendation(str, Enum):
    GO = "go"
    NO_GO = "no-go"
    ITERATE = "iterate"


class Cost(BaseModel):
    """Cost accounting attached to every Result."""

    tokens: int = 0
    usd: float = 0.0

    def __add__(self, other: "Cost") -> "Cost":
        return Cost(tokens=self.tokens + other.tokens, usd=round(self.usd + other.usd, 6))


class TaskEnvelope(BaseModel):
    """A unit of work handed from one role to another."""

    task_id: str
    sender: str = Field(..., alias="from")
    recipient: str = Field(..., alias="to")
    objective: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    deadline: str | None = None
    priority: Priority = Priority.P1
    context_refs: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class ResultEnvelope(BaseModel):
    """What a role returns after working a task."""

    task_id: str
    status: ResultStatus
    summary: str
    artifacts: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    blockers: list[str] = Field(default_factory=list)
    recommendation: Recommendation | None = None
    cost: Cost = Field(default_factory=Cost)


class Idea(BaseModel):
    """A single business idea / hypothesis produced by the Idea Generator."""

    id: str
    title: str
    hypothesis: str
    target_audience: str = ""
    rationale: str = ""
