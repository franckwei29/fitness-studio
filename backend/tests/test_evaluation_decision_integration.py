"""A decision to retain service resolves the inbox without approving a release."""

from uuid import uuid4

from test_admission_integration import owner_client as owner_fixture

from app.bootstrap import DEFAULT_CONFIGURATION
from app.database import transaction
from app.models import ChangeRequest, EvaluationRun, MemberGroup
from app.settings import get_settings

owner_client = owner_fixture


def prepare(client):
    with transaction() as db:
        group = MemberGroup(name=f"Decision-{uuid4()}", configuration=DEFAULT_CONFIGURATION)
        db.add(group)
        db.flush()
        group_id = group.id
    other = "gpt-6-luna" if get_settings().default_model != "gpt-6-luna" else "gemini-3.5-flash-lite"
    response = client.post(
        "/api/owner/agent-versions/compare-current", json={"group_id": group_id, "model": other}
    )
    assert response.status_code == 201, response.text
    run = response.json()
    path = f"/api/owner/agent-versions/{run['candidate_id']}/evaluations/{run['id']}"
    return group_id, run, path


def test_keep_current_resolves_update_without_changing_evidence_or_group(owner_client):
    group, run, path = prepare(owner_client)
    detail = owner_client.get(path).json()
    assert (
        owner_client.post(
            path + "/keep-current", json={"fingerprint": detail["decision_fingerprint"]}
        ).status_code
        == 409
    )
    with transaction() as db:
        db.get(
            EvaluationRun, run["id"]
        ).status = "Reviewed"  # Legacy record, no fabricated provider evidence.
    before = owner_client.get("/api/owner/updates").json()
    row = next(r for r in before["items"] if r["item"]["id"] == run["id"])
    assert row["state"] == "Needs action"
    detail = owner_client.get(path).json()
    assert owner_client.post(path + "/keep-current", json={"fingerprint": "old"}).status_code == 409
    for _ in range(2):
        saved = owner_client.post(
            path + "/keep-current", json={"fingerprint": detail["decision_fingerprint"]}
        )
        assert saved.status_code == 200, saved.text
    for _ in range(2):
        updates = owner_client.get("/api/owner/updates").json()
        assert next(r for r in updates["items"] if r["id"] == row["id"])["state"] == "Resolved"
    with transaction() as db:
        assert db.get(MemberGroup, group).configuration == DEFAULT_CONFIGURATION
        assert db.get(EvaluationRun, run["id"]).status == "Reviewed"
    # New comparison still needs a fresh decision.
    _, second, _ = prepare(owner_client)
    with transaction() as db:
        db.get(EvaluationRun, second["id"]).status = "Incomplete"
    updates = owner_client.get("/api/owner/updates").json()
    assert next(r for r in updates["items"] if r["item"]["id"] == second["id"])["state"] == "Needs action"


def test_release_handoff_resolves_evaluation_and_prevents_conflicting_keep(owner_client):
    group, run, path = prepare(owner_client)
    with transaction() as db:
        db.get(EvaluationRun, run["id"]).status = "Completed"
    updates = owner_client.get("/api/owner/updates").json()
    row = next(r for r in updates["items"] if r["item"]["id"] == run["id"])
    with transaction() as db:
        change = ChangeRequest(
            kind="AgentRelease",
            group_id=group,
            baseline={},
            candidate={"evaluation_id": run["id"]},
            fingerprint="f" * 64,
            status="Rejected",
            reason="Fixture release decision",
        )
        db.add(change)
        db.flush()
        release_id = change.id
    detail = owner_client.get(path).json()
    assert detail["release"]["id"] == release_id
    assert (
        owner_client.post(
            path + "/keep-current", json={"fingerprint": detail["decision_fingerprint"]}
        ).status_code
        == 409
    )
    updates = owner_client.get("/api/owner/updates").json()
    assert next(r for r in updates["items"] if r["id"] == row["id"])["state"] == "Resolved"
