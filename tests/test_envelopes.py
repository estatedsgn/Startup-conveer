import pytest
from pydantic import ValidationError

from conveer.envelopes import (
    Cost,
    Priority,
    Recommendation,
    ResultEnvelope,
    ResultStatus,
    TaskEnvelope,
)


def test_task_envelope_aliases_from_to():
    t = TaskEnvelope(
        task_id="t1",
        **{"from": "ceo", "to": "analyst"},
        objective="design a test",
    )
    assert t.sender == "ceo"
    assert t.recipient == "analyst"
    assert t.priority is Priority.P1  # default


def test_result_envelope_roundtrip():
    r = ResultEnvelope(
        task_id="t1",
        status=ResultStatus.DONE,
        summary="ok",
        confidence=0.8,
        recommendation=Recommendation.GO,
        cost=Cost(tokens=10, usd=0.01),
    )
    assert r.recommendation is Recommendation.GO
    assert r.cost.tokens == 10


def test_cost_addition():
    total = Cost(tokens=5, usd=0.02) + Cost(tokens=7, usd=0.03)
    assert total.tokens == 12
    assert total.usd == 0.05


def test_confidence_bounds_enforced():
    with pytest.raises(ValidationError):
        ResultEnvelope(task_id="t", status=ResultStatus.DONE, summary="x", confidence=2)
