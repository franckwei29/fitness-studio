"""A bounded agent workflow with durable Redis checkpoints and explicit waits."""

from contextlib import contextmanager
from typing import Callable, TypedDict

from langgraph.checkpoint.redis import RedisSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from langsmith import tracing_context

from app.settings import get_settings


class AgentState(TypedDict, total=False):
    request_id: str
    work_id: str
    role: str
    input_versions: dict
    context: dict
    candidate: dict
    validation: dict
    requires_confirmation: bool
    confirmation: dict
    outcome: dict


def confirmation_node(state: AgentState):
    answer = interrupt(
        {
            "kind": "CandidateConfirmation",
            "request_id": state["request_id"],
            "role": state["role"],
            "input_versions": state.get("input_versions", {}),
            "candidate": state["candidate"],
        }
    )
    if not isinstance(answer, dict) or answer.get("decision") not in {"Confirm", "Revise", "Cancel"}:
        raise ValueError("A valid confirmation decision is required.")
    return {"confirmation": answer}


def build_graph(checkpointer, *, prepare: Callable, generate: Callable, validate: Callable, commit: Callable):
    """Handlers enforce database permissions and idempotency at every boundary.

    Checkpoints support orchestration, but never authorize an action. In
    particular, commit must re-read current facts and verify the exact approved
    candidate before producing any persistent business effect.
    """
    graph = StateGraph(AgentState)
    graph.add_node("prepare", prepare)
    graph.add_node("generate", generate)
    graph.add_node("validate", validate)
    graph.add_node("confirmation", confirmation_node)
    graph.add_node("commit", commit)
    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "generate")
    graph.add_edge("generate", "validate")
    graph.add_conditional_edges(
        "validate",
        lambda state: "confirmation" if state.get("requires_confirmation") else "commit",
        {"confirmation": "confirmation", "commit": "commit"},
    )
    graph.add_edge("confirmation", "commit")
    graph.add_edge("commit", END)
    return graph.compile(checkpointer=checkpointer)


@contextmanager
def persistent_graph(**handlers):
    # Raw graph state contains health facts. Export only the explicit allowlisted
    # operational snapshot, even if ambient LANGSMITH_TRACING is enabled.
    with tracing_context(enabled=False), RedisSaver.from_conn_string(
        get_settings().redis_url,
        checkpoint_prefix="studio_checkpoint",
        checkpoint_write_prefix="studio_checkpoint_write",
    ) as saver:
        saver.setup()
        yield build_graph(saver, **handlers)


def graph_config(work_id):
    return {"configurable": {"thread_id": work_id}, "recursion_limit": 12}
