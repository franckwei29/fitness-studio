"""Only an explicitly selected idle legacy draft is adopted; work and approvals stay intact."""

from datetime import timedelta

from sqlalchemy import select
from test_owner_workspace_integration import workspace_clients  # noqa: F401
from test_sage_experience_integration import current_plans

from app.activation import plan_fingerprint
from app.database import transaction
from app.models import Member, PlanVersion, Request, Workflow, WorkItem, now


def test_selected_legacy_adoption_requires_ownership_and_idle_work(workspace_clients):  # noqa: F811
    owner, member = workspace_clients
    current_plans(member)
    user_id = member.get("/api/auth/me").json()["user"]["id"]
    with transaction() as db:
        person = db.scalar(select(Member).where(Member.user_id == user_id))
        request = Request(
            user_id=user_id,
            session_id=member.post("/api/conversations").json()["id"],
            kind="Training",
            status="Completed",
            deadline=now() + timedelta(hours=1),
            payload={"content": "Saved training draft"},
        )
        db.add(request)
        db.flush()
        plan = PlanVersion(
            member_id=person.id,
            request_id=request.id,
            kind="Training",
            version=2,
            status="Validated",
            profile_version=person.profile_version,
            config_version=1,
            agent_version_id="test",
            content={"title": "Saved draft"},
            rationale="Test evidence",
            fingerprint="pending",
        )
        db.add(plan)
        db.flush()
        plan.fingerprint = plan_fingerprint(plan)
        work = WorkItem(
            request_id=request.id,
            role="Coach",
            subject_kind="Conversation",
            subject_id=request.session_id,
            status="Queued",
        )
        db.add(work)
        db.flush()
        rid, wid = request.id, work.id
    path = f"/api/me/workflows/adopt/{rid}"
    assert owner.post(path, json={}).status_code == 404
    assert member.post(path, json={}).status_code == 409
    with transaction() as db:
        assert db.scalar(select(Workflow).where(Workflow.request_id == rid)) is None
        assert db.get(WorkItem, wid).status == "Queued"
        db.get(WorkItem, wid).status = "Completed"
    first = member.post(path, json={})
    assert first.status_code == 200, first.text
    assert first.json()["stage"] == "TrainingDiscussion"
    assert member.post(path, json={}).json()["id"] == first.json()["id"]
    assert owner.get("/api/me/workflows/" + first.json()["id"]).status_code == 404
    with transaction() as db:
        assert db.get(WorkItem, wid).status == "Completed"
