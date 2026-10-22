"""Bounded SSE replay resumes in persisted order across multiple batches."""

from uuid import uuid4

from fastapi.testclient import TestClient
from test_chat_integration import register

from app.database import transaction
from app.main import app
from app.models import RequestEvent


def sequences(response):
    assert response.status_code == 200
    return [int(line[4:]) for line in response.text.splitlines() if line.startswith("id: ")]


def test_reconnect_replays_remaining_events_without_duplicates():
    with TestClient(app) as client:
        register(client)
        conversation = client.post("/api/conversations").json()["id"]
        request_id = client.post(
            f"/api/conversations/{conversation}/messages",
            json={
                "content": "Synthetic replay request",
                "key": str(uuid4()),
            },
        ).json()["request_id"]
        with transaction() as db:
            for index in range(120):
                db.add(RequestEvent(request_id=request_id, event_type="processing", payload={"index": index}))
        endpoint = f"/api/requests/{request_id}/events"
        first = sequences(client.get(endpoint))
        assert len(first) == 100 and first == sorted(set(first))
        second = sequences(client.get(endpoint, headers={"Last-Event-ID": str(first[-1])}))
        assert len(second) == 21 and second == sorted(set(second)) and second[0] > first[-1]
        assert sequences(client.get(endpoint, headers={"Last-Event-ID": str(second[-1])})) == []
        assert (
            sequences(client.get(f"{endpoint}?after={second[-1]}", headers={"Last-Event-ID": str(first[-1])}))
            == []
        )
        snapshot = client.get(f"/api/conversations/{conversation}").json()
        assert snapshot["event_cursor"] == second[-1]
