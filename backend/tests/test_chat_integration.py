"""Conversation persistence, ownership, and replay checks."""

from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


def register(client):
    response = client.post(
        "/api/auth/register",
        json={
            "email": f"chat-{uuid4()}@example.test",
            "password": "Integration-test-123!",
            "display_name": "Chat Member",
        },
    )
    assert response.status_code == 201
    client.headers["x-csrf-token"] = response.json()["csrf_token"]


def test_submission_replay_and_private_event_history():
    with TestClient(app) as client, TestClient(app) as other:
        register(client)
        register(other)
        session = client.post("/api/conversations").json()
        url = f"/api/conversations/{session['id']}"
        body = {"content": "我每周可以训练三天", "key": str(uuid4()), "purpose": "Chat"}
        response = client.post(f"{url}/messages", json=body)
        assert response.status_code == 202
        result = response.json()
        assert client.post(f"{url}/messages", json=body).json() == result
        assert client.post(f"{url}/messages", json=body | {"content": "Different"}).status_code == 409
        history = client.get(url).json()
        assert len(history["messages"]) == 1
        assert history["messages"][0]["content"] == body["content"]
        assert len(history["requests"]) == 1
        assert history["requests"][0]["payload"]["role"] == "Intake"
        request_url = f"/api/requests/{result['request_id']}"
        stream = client.get(f"{request_url}/events")
        assert stream.status_code == 200
        assert "event: queued" in stream.text
        cursor = next(line.split(": ")[1] for line in stream.text.splitlines() if line.startswith("id:"))
        assert "event: queued" not in client.get(f"{request_url}/events?after={cursor}").text
        assert other.get(url).status_code == 404
        assert other.get(request_url).status_code == 404
        assert other.get(f"{request_url}/events").status_code == 404
        assert other.post(f"{url}/messages", json=body).status_code == 404
        assert (
            client.post(
                f"{url}/messages", json=body | {"key": str(uuid4()), "purpose": "Training"}
            ).status_code
            == 409
        )
        assert (
            client.post(
                f"{url}/messages", json=body | {"key": str(uuid4()), "purpose": "Operations"}
            ).status_code
            == 403
        )


def test_accepted_member_can_explicitly_return_to_intake():
    from sqlalchemy import select

    from app.database import transaction
    from app.models import Member, MemberGroup

    with TestClient(app) as client:
        register(client)
        user_id = client.get("/api/auth/me").json()["user"]["id"]
        with transaction() as db:
            group = db.scalar(select(MemberGroup).where(MemberGroup.name == "Default"))
            db.add(Member(user_id=user_id, group_id=group.id, status="Accepted"))
        session = client.post("/api/conversations").json()
        response = client.post(
            f"/api/conversations/{session['id']}/messages",
            json={
                "key": str(uuid4()),
                "purpose": "Intake",
                "content": "Help me review changed availability.",
            },
        )
        assert response.status_code == 202
        history = client.get(f"/api/conversations/{session['id']}").json()
        assert history["requests"][0]["payload"]["role"] == "Intake"
