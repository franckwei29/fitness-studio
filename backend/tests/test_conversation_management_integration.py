from datetime import timedelta

from sqlalchemy import select
from test_owner_workspace_integration import workspace_clients  # noqa: F401

from app.database import transaction
from app.models import ChatSession, Request, now


def test_conversation_rename_and_soft_delete(workspace_clients):  # noqa: F811
    owner, member = workspace_clients
    session = member.post("/api/conversations").json()
    path = "/api/conversations/" + session["id"]
    assert owner.post(path + "/rename", json={"title": "Other account"}).status_code == 404
    assert owner.post(path + "/delete", json={}).status_code == 404
    assert member.post(path + "/rename", json={"title": "   "}).status_code == 422
    assert member.post(path + "/rename", json={"title": " My training "}).json()["title"] == "My training"
    uid = member.get("/api/auth/me").json()["user"]["id"]
    with transaction() as db:
        task = Request(
            user_id=uid,
            session_id=session["id"],
            kind="Training",
            status="AwaitingConfirmation",
            deadline=now() + timedelta(hours=1),
        )
        db.add(task)
        db.flush()
        rid = task.id
    assert member.post(path + "/delete", json={}).status_code == 409
    with transaction() as db:
        db.get(Request, rid).status = "Cancelled"
    assert member.post(path + "/delete", json={}).status_code == 200
    assert member.post(path + "/delete", json={}).status_code == 200
    assert not any(r["id"] == session["id"] for r in member.get("/api/conversations").json())
    assert member.get(path).status_code == 404
    assert (
        member.post(
            path + "/messages", json={"key": "deleted-test-key", "content": "Hello", "purpose": "Auto"}
        ).status_code
        == 404
    )
    with transaction() as db:
        assert db.get(ChatSession, session["id"]).deleted_at
        assert db.scalar(select(Request).where(Request.id == rid)) is not None
