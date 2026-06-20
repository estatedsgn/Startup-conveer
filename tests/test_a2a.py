from conveer import a2a


def test_text_and_data_messages():
    m = a2a.text_message("agent", "hello", sender="chief", recipient="research_lead")
    assert m.text() == "hello"
    assert m.sender == "chief"
    d = a2a.data_message("agent", {"x": 1})
    assert d.data() == {"x": 1}


def test_artifact_data():
    art = a2a.artifact_from_data("res", {"k": "v"})
    assert art.data() == {"k": "v"}
    assert art.name == "res"


def test_jsonrpc_envelope():
    req = a2a.send_request(a2a.text_message("agent", "hi"))
    assert req.jsonrpc == "2.0"
    assert req.method == "message/send"
    assert "message" in req.params


def test_task_defaults():
    t = a2a.Task(objective="do x", sender="a", recipient="b")
    assert t.status is a2a.TaskStatus.SUBMITTED
    assert t.id.startswith("task-")


def test_parse_agent_output_tolerant():
    out = a2a.parse_agent_output('noise {"plan": [{"to": "x"}]} tail')
    assert out["plan"][0]["to"] == "x"
