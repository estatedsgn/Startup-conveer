from conveer.memory import SHARED, Memory


def test_remember_and_recall(tmp_path):
    mem = Memory(tmp_path)
    mem.remember("freelancers hate proposals", scope=SHARED, tags=["pain"])
    mem.remember("idea-1 picked", scope="analyst", tags=["decision"])
    shared = mem.recall(scope=SHARED)
    assert shared and shared[-1]["text"] == "freelancers hate proposals"
    assert mem.recall(scope="analyst")[0]["text"] == "idea-1 picked"


def test_recall_query_and_tags(tmp_path):
    mem = Memory(tmp_path)
    mem.remember("pricing is $300/mo", scope=SHARED, tags=["pricing"])
    mem.remember("audience is seed founders", scope=SHARED, tags=["icp"])
    assert len(mem.recall(scope=SHARED, query="pricing")) == 1
    assert len(mem.recall(scope=SHARED, tags=["icp"])) == 1


def test_context_for_blends_scopes(tmp_path):
    mem = Memory(tmp_path)
    mem.remember("company fact", scope=SHARED)
    mem.remember("my note", scope="copywriter")
    ctx = mem.context_for("copywriter")
    assert "[company] company fact" in ctx
    assert "[you] my note" in ctx
