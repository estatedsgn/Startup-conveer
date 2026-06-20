from conveer import config, prompt_store


def _settings(tmp_path):
    return config.Settings(prompts_dir=tmp_path / "prompts")


def test_baseline_when_no_versions(tmp_path):
    s = _settings(tmp_path)
    pv = prompt_store.current_prompt("ceo", s)
    assert pv.version == 0
    assert pv.label == "v0-baseline"
    assert "CEO" in pv.text


def test_save_and_activate_new_version(tmp_path):
    s = _settings(tmp_path)
    v1 = prompt_store.save_version("ceo", "NEW PROMPT", score=0.5, rationale="x", settings=s)
    assert v1.version == 1
    cur = prompt_store.current_prompt("ceo", s)
    assert cur.version == 1
    assert cur.text == "NEW PROMPT"


def test_versions_increment_and_history(tmp_path):
    s = _settings(tmp_path)
    prompt_store.save_version("analyst", "P1", score=0.6, settings=s)
    prompt_store.save_version("analyst", "P2", score=0.8, settings=s)
    assert prompt_store.current_prompt("analyst", s).version == 2
    hist = prompt_store.history("analyst", s)
    assert [h["version"] for h in hist] == [1, 2]


def test_rollback_to_baseline(tmp_path):
    s = _settings(tmp_path)
    prompt_store.save_version("reporter", "EVOLVED", settings=s)
    prompt_store.set_current("reporter", 0, s)
    assert prompt_store.current_prompt("reporter", s).version == 0
