import json

from langgraph.types import Command

from conveer import graph as G
from conveer.graph import extract_json, resolve_selection, select_greenlit
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
    # all decisions were "iterate" -> venture studio is gated off, no exec plan
    assert res.get("greenlit_ideas", []) == []
    assert "execution_plan" not in res


def test_select_greenlit_picks_only_go():
    state = {
        "selected_ideas": [{"id": "idea-1"}, {"id": "idea-2"}, {"id": "idea-3"}],
        "decision": {
            "decisions": [
                {"idea_id": "idea-1", "decision": "go"},
                {"idea_id": "idea-2", "decision": "iterate"},
                {"idea_id": "idea-3", "decision": "GO"},  # case-insensitive
            ]
        },
    }
    assert select_greenlit(state) == [{"id": "idea-1"}, {"id": "idea-3"}]


def _fake_invoke_with_go(role, prompt, **kwargs):
    """Like _fake_invoke but the CEO green-lights idea-1, and the venture
    studio specialists return plausible JSON objects."""
    if role == "ceo":
        return RunResult(
            text=json.dumps(
                {
                    "summary": "one to build",
                    "decisions": [
                        {"idea_id": "idea-1", "title": "T1", "decision": "go", "next_step": "build"},
                        {"idea_id": "idea-2", "decision": "no-go", "next_step": "drop"},
                    ],
                    "priority_order": ["idea-1"],
                    "needs_human": [],
                }
            ),
            session_id="ceo",
        )
    if role in {"market_researcher", "product_manager", "tech_lead",
                "growth_marketer", "finance"}:
        return RunResult(text=json.dumps({"note": f"{role} plan"}), session_id=role)
    if role == "coo":
        return RunResult(
            text=json.dumps(
                {
                    "summary": "ship idea-1",
                    "ventures": [
                        {"idea_id": "idea-1", "title": "T1", "readiness": "ready",
                         "first_30_days": ["build mvp"], "owner": "tech_lead",
                         "kpis": ["signups"], "key_risk": "distribution"}
                    ],
                    "resource_plan": "1 eng + agents",
                    "recommended_focus": "idea-1",
                    "needs_human": ["approve budget"],
                }
            ),
            session_id="coo",
        )
    return _fake_invoke(role, prompt, **kwargs)


def test_graph_venture_studio_runs_for_greenlit(monkeypatch):
    monkeypatch.setattr(G, "invoke_role", _fake_invoke_with_go)
    graph = G.build_graph()
    cfg = {"configurable": {"thread_id": "t-venture"}}

    graph.invoke({"run_id": "t-venture", "topic": "x", "num_ideas": 3}, cfg)
    graph.invoke(Command(resume=["idea-1", "idea-2"]), cfg)
    res = graph.invoke(Command(resume={"__skip__": True}), cfg)

    assert "__interrupt__" not in res or not res["__interrupt__"]
    assert [i["id"] for i in res["greenlit_ideas"]] == ["idea-1"]
    assert len(res["market_research"]) == 1
    assert len(res["product_plans"]) == 1
    assert len(res["tech_assessments"]) == 1
    assert len(res["gtm_plans"]) == 1
    assert len(res["finance_models"]) == 1
    assert res["execution_plan"]["recommended_focus"] == "idea-1"
    assert res["execution_plan"]["ventures"][0]["idea_id"] == "idea-1"
