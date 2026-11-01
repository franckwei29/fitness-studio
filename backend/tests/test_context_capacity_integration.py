"""Full payload capacity is checked before any generation reservation or dispatch."""
import pytest
from pydantic import SecretStr
from test_work_queue_integration import task as task_fixture
from app.agent_contracts import ChatReply
from app.model_gateway import generate, ModelUnavailable
from app.settings import get_settings
from app.work_queue import claim_next

task = task_fixture

@pytest.mark.parametrize('count,expected', [(1022952, 'allowed'), (1022953, 'ContextCapacityExceeded')])
def test_context_boundary_precedes_generation(monkeypatch, task, count, expected):
    settings = get_settings().model_copy(update={'model_provider':'openai','model_calls_enabled':True,'openai_api_key':SecretStr('synthetic-not-sent')})
    monkeypatch.setattr('app.model_gateway.get_settings', lambda: settings)
    monkeypatch.setattr('app.model_gateway.OpenAITransport.count_input_tokens', lambda self,payload:count)
    class ReachedReservation(Exception): pass
    def reserve(*a, **k): raise ReachedReservation()
    monkeypatch.setattr('app.model_gateway.reserve',reserve)
    claim=claim_next(request_id=task[0])
    if expected == 'allowed':
        with pytest.raises(ReachedReservation):
            generate(claim,'Reply.',{'text':'x'*70000},ChatReply)
    else:
        with pytest.raises(ModelUnavailable) as exc:
            generate(claim,'Reply.',{'text':'x'*70000},ChatReply)
        assert exc.value.wait_reason == expected
