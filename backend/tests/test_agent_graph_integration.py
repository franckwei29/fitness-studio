"""Redis-backed graph interruption and recovery across executor recreation."""

from uuid import uuid4

from langgraph.types import Command
from langsmith import tracing_context
from langsmith.run_helpers import get_tracing_context

from app.agent_graph import graph_config, persistent_graph


def test_wait_survives_executor_recreation_without_regeneration():
    generated = []
    committed = []
    work_id = str(uuid4())
    config = graph_config(work_id)

    def prepare(state):
        return {"context": {"goal": "Build consistency"}, "input_versions": {"profile": 1}}

    def generate(state):
        generated.append(state["work_id"])
        return {"candidate": {"reply": "Please confirm your goal."}}

    def validate(state):
        return {"validation": {"passed": True}, "requires_confirmation": True}

    def commit(state):
        committed.append(state["confirmation"]["decision"])
        return {"outcome": {"status": "Confirmed"}}

    handlers = dict(prepare=prepare, generate=generate, validate=validate, commit=commit)
    with persistent_graph(**handlers) as graph:
        assert get_tracing_context()["enabled"] is False
        result = graph.invoke({"request_id": str(uuid4()), "work_id": work_id, "role": "Intake"}, config)
        assert result["__interrupt__"]
        assert generated == [work_id]
        assert committed == []
    with persistent_graph(**handlers) as graph:
        snapshot = graph.get_state(config)
        assert snapshot.next == ("confirmation",)
        result = graph.invoke(Command(resume={"decision": "Confirm"}), config)
        assert result["outcome"]["status"] == "Confirmed"
        assert generated == [work_id]
        assert committed == ["Confirm"]
        assert graph.get_state(config).next == ()
        graph.checkpointer.delete_thread(work_id)


def test_ambient_cloud_tracing_is_disabled_for_private_graph_state(monkeypatch):
    exported = []
    monkeypatch.setattr("langsmith.Client.create_run", lambda *args, **kwargs: exported.append(kwargs))
    handlers = dict(prepare=lambda _: {}, generate=lambda _: {}, validate=lambda _: {}, commit=lambda _: {})
    with tracing_context(enabled=True):
        with persistent_graph(**handlers) as graph:
            assert get_tracing_context()["enabled"] is False
            work_id = str(uuid4())
            graph.invoke(
                {
                    "request_id": str(uuid4()),
                    "work_id": work_id,
                    "role": "Intake",
                    "context": {"private": "SYNTHETIC_HEALTH_FACT"},
                },
                graph_config(work_id),
            )
            graph.checkpointer.delete_thread(work_id)
        assert get_tracing_context()["enabled"] is True
    assert exported == []
