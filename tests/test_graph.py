import json

from langgraph.types import Command

from conveer import graph as G
from conveer.graph import extract_json, resolve_selection
from conveer.runner import RunResult


def test_extract_json_with_fences():
    txt = "```json\n[{\"id\": \"idea-1\"}]\n```"
    assert extract_json(txt, "array") == [{"id": "idea-1"}]


def test_extract_json_with_surrounding_prose():
    txt = 'Here you go:\n{"summary": "x"}\nThanks!'
    assert extract_json(txt, "object") == {"summary": "x"}


def test_resolve_selection_by_id_and_index():
    ideas = [{"id": "idea-1"}, {"id": "idea-2"}, {"id": "idea-3"}]
    assert resolve_selection(ideas, ["idea-2", "3"]) == [
        {"id": "idea-2"},
        {"id": "idea-3"},
    ]


def _fake_invoke(role, prompt, **kwargs):
    if role == "idea_generator":
        ideas = [{"id": f"idea-{i}", "title": f"T{i}", "hypothesis": "h"} for i in range(1, 4)]
        return RunResult(text=json.dumps(ideas), session_id="ig")
    if role == "analyst":
        return RunResult(text=json.dumps({"metrics": [{"name": "signup", "go_threshold": "5"}]}), session_id="an")
    if role == "reporter":
        return RunResult(text="# Validation Report\n- ok", session_id="rp")
    if role == "ceo":
        return RunResult(
            text=json.dumps(
                {
                    "summary": "two to iterate",
                    "decisions": [
                        {"idea_id": "idea-1", "decision": "iterate", "next_step": "test"},
                        {"idea_id": "idea-2", "decision": "iterate", "next_step": "test"},
                    ],
                    "priority_order": ["idea-1", "idea-2"],
                    "needs_human": [],
                }
            ),
            session_id="ceo",
        )
    raise AssertionError(f"unexpected role {role}")


def test_graph_reaches_first_interrupt(monkeypatch):
    monkeypatch.setattr(G, "invoke_role", _fake_invoke)
    graph = G.build_graph()
    cfg = {"configurable": {"thread_id": "t-interrupt"}}
    res = graph.invoke({"run_id": "t-interrupt", "topic": "x", "num_ideas": 3}, cfg)

    assert res.get("__interrupt__"), "graph should pause at idea selection"
    assert len(res["ideas"]) == 3
    assert res["__interrupt__"][0].value["type"] == "select_ideas"


def test_graph_full_flow_completes(monkeypatch):
    monkeypatch.setattr(G, "invoke_role", _fake_invoke)
    graph = G.build_graph()
    cfg = {"configurable": {"thread_id": "t-full"}}

    res = graph.invoke({"run_id": "t-full", "topic": "x", "num_ideas": 3}, cfg)
    assert res["__interrupt__"][0].value["type"] == "select_ideas"

    res = graph.invoke(Command(resume=["idea-1", "idea-2"]), cfg)
    assert res["__interrupt__"][0].value["type"] == "enter_custdev_results"

    res = graph.invoke(Command(resume={"__skip__": True}), cfg)  # skip results
    assert "__interrupt__" not in res or not res["__interrupt__"]
    assert res["custdev_results"] == {}
    assert len(res["selected_ideas"]) == 2
    assert len(res["methodologies"]) == 2
    assert res["report_md"].startswith("# Validation Report")
    assert res["decision"]["summary"] == "two to iterate"
    assert res["cost"]["tokens"] >= 0
