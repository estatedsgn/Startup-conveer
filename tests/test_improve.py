import json

from conveer import config, graph, prompt_store
from conveer.improve import improve_role
from conveer.registry import improvable_roles
from conveer.runner import RunResult


def test_control_plane_excluded_from_improvable():
    roles = improvable_roles()
    assert "critic" not in roles and "coach" not in roles
    assert "analyst" in roles


def test_improve_loop_learns_and_selects_best(tmp_path, monkeypatch):
    s = config.Settings(prompts_dir=tmp_path / "prompts")
    calls = {"critic": 0}

    def fake_invoke(role, prompt, **kwargs):
        if role == "critic":
            calls["critic"] += 1
            score = 0.4 if calls["critic"] == 1 else 0.9
            return RunResult(text=json.dumps({
                "score": score,
                "passed": score >= 0.8,
                "summary": "verdict",
                "fundamental_issues": [{"principle": "be specific", "why_it_matters": "always"}],
                "strengths": [],
            }))
        if role == "coach":
            return RunResult(text=json.dumps({
                "improved_prompt": "IMPROVED PROMPT",
                "change_rationale": "added a generalizable specificity principle",
                "changes": ["principle-level edit"],
            }))
        # target role doing its work
        return RunResult(text="raw work output")

    monkeypatch.setattr(graph, "invoke_role", fake_invoke)

    final = improve_role(
        "analyst", "Design a validation for idea X",
        bar=0.8, max_rounds=3, settings=s, thread_id="t-improve",
    )

    # Two rounds: baseline scored 0.4, then coached version scored 0.9.
    assert len(final["history"]) == 2
    assert final["history"][0]["version"] == "v0-baseline"
    assert final["history"][1]["version"] == "v1"
    assert final["score"] == 0.9
    assert final["selected_version"] == 1
    # The improved prompt is persisted and active.
    assert prompt_store.current_prompt("analyst", s).text == "IMPROVED PROMPT"


def test_improve_stops_at_max_rounds(tmp_path, monkeypatch):
    s = config.Settings(prompts_dir=tmp_path / "prompts")

    def always_bad(role, prompt, **kwargs):
        if role == "critic":
            return RunResult(text=json.dumps({"score": 0.2, "fundamental_issues": []}))
        if role == "coach":
            return RunResult(text=json.dumps({"improved_prompt": "STILL TRYING"}))
        return RunResult(text="output")

    monkeypatch.setattr(graph, "invoke_role", always_bad)
    final = improve_role(
        "reporter", "task", bar=0.9, max_rounds=2, settings=s, thread_id="t-bad",
    )
    # rounds 0,1,2 critiqued -> 3 history entries, never passed
    assert len(final["history"]) == 3
    assert all(not e["passed"] for e in final["history"])
