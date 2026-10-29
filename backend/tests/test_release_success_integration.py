"""Protocol simulation of the successful evaluation/review/canary path.

Synthetic provider records exist only in the isolated test database. They are
not evidence of a live model evaluation or authorization for a real release.
"""

from uuid import uuid4

from sqlalchemy import select
from test_admission_integration import owner_client as owner_fixture

from app.admission import fingerprint
from app.agent_contracts import ChatReply
from app.database import transaction
from app.model_payload import build_payload
from app.models import AgentVersion, ModelCall, Request
from app.pricing import price_for
from app.settings import get_settings
from app.version_admin import base_instruction
from app.worker import run_once

owner_client = owner_fixture


def test_successful_review_and_canary_execution(monkeypatch, owner_client):
    def simulated(claim, instruction, context, output_type, max_output_tokens):
        output = ChatReply(
            reply="The recorded evidence requires owner review. Unknown usage has no determined cost. No repair or deployment is confirmed."
        )
        model = get_settings().gemini_model
        usage = dict(
            total_input_tokens=10,
            total_output_tokens=10,
            total_tokens=20,
            total_thought_tokens=0,
            total_cached_tokens=0,
            total_tool_use_tokens=0,
        )
        with transaction() as db:
            request = db.get(Request, claim.request_id)
            request.model_calls += 1
            call = ModelCall(
                request_id=request.id,
                user_id=request.user_id,
                role=claim.role,
                model=model,
                status="Settled",
                reserved_micros=0,
                actual_micros=price_for(model).actual(usage),
                price_version=price_for(model).version,
                usage=usage,
                output=output.model_dump(),
                request_fingerprint=fingerprint(
                    {
                        "work_id": claim.work_id,
                        "payload": build_payload(
                            model,
                            instruction,
                            context,
                            output_type,
                            max_output_tokens,
                            version=db.get(AgentVersion, context["agent_version_id"]),
                        ),
                    }
                ),
            )
            db.add(call)
            db.flush()
            return output, call.id

    monkeypatch.setattr("app.evaluation_runtime.generate", simulated)
    model = get_settings().gemini_model
    prompt = base_instruction("TechnicalManager")
    digest = fingerprint(dict(role="TechnicalManager", model=model, prompt=prompt))
    with transaction() as db:
        baseline = db.scalar(select(AgentVersion).where(AgentVersion.fingerprint == digest))
        if not baseline:
            baseline = AgentVersion(
                role="TechnicalManager",
                model=model,
                prompt=prompt,
                fingerprint=digest,
                label="Runtime baseline",
            )
            db.add(baseline)
            db.flush()
        baseline_id = baseline.id
    response = owner_client.post(
        "/api/owner/agent-versions",
        json=dict(
            role="TechnicalManager",
            label=str(uuid4()),
            model=model,
            instructions="Explain uncertainty clearly.",
        ),
    )
    assert response.status_code == 201, response.text
    versions = [baseline_id, response.json()["id"]]
    path = f"/api/owner/agent-versions/{versions[1]}/evaluations"
    run = owner_client.post(path, json={"baseline_id": versions[0]}).json()
    start = owner_client.post(f"{path}/{run['id']}/start").json()
    for request_id in start["summary"]["request_ids"]:
        assert run_once(request_id)
    detail = owner_client.get(f"{path}/{run['id']}").json()
    assert detail["run"]["status"] == "AwaitingReview", detail
    review = owner_client.post(
        f"{path}/{run['id']}/review",
        json={
            "ratings": [
                dict(
                    result_id=r["id"],
                    explanation_quality=4,
                    factual_and_role_compliant=True,
                    notes="Synthetic protocol verification, no live quality claim.",
                )
                for r in detail["results"]
            ]
        },
    )
    assert review.status_code == 200, review.text
    group = next(g for g in owner_client.get("/api/owner/groups").json() if g["name"] == "Canary")
    from app.models import MemberGroup

    with transaction() as db:
        target_group = db.get(MemberGroup, group["id"])
        target_group.configuration = target_group.configuration | {
            "agent_versions": {"TechnicalManager": baseline_id}
        }
        target_group.version += 1
        group["version"] = target_group.version
    proposal = owner_client.post(
        "/api/owner/releases",
        json=dict(
            group_id=group["id"],
            expected_group_version=group["version"],
            evaluation_id=run["id"],
            reason="Synthetic release protocol verification.",
        ),
    )
    assert proposal.status_code == 201, proposal.text
    release = proposal.json()
    endpoint = f"/api/owner/releases/{release['id']}"
    response = owner_client.post(
        endpoint + "/review", json=dict(decision="Approved", fingerprint=release["fingerprint"])
    )
    assert response.status_code == 200, response.text
    applied = owner_client.post(endpoint + "/execute")
    assert applied.status_code == 200, applied.text
    assert applied.json()["status"] == "Canary"
    assert owner_client.post(endpoint + "/execute").json() == applied.json()
    # Advance the observation window without sleeping or making provider calls.
    from copy import deepcopy
    from datetime import timedelta

    from app.bootstrap import DEFAULT_CONFIGURATION
    from app.canary_monitor import monitor_canaries
    from app.models import ChangeRequest, MemberGroup, WorkItem, now

    with transaction() as db:
        change = db.get(ChangeRequest, release["id"])
        change.execution = change.execution | {"started_at": (now() - timedelta(minutes=31)).isoformat()}
        for _ in range(3):
            observed_request = Request(
                user_id=run["created_by"],
                kind="Operations",
                status="Completed",
                deadline=now() + timedelta(hours=1),
            )
            db.add(observed_request)
            db.flush()
            db.add(
                WorkItem(
                    request_id=observed_request.id,
                    role="TechnicalManager",
                    subject_kind="Conversation",
                    subject_id=str(uuid4()),
                    status="Completed",
                    input_versions={
                        "group_id": group["id"],
                        "group": change.execution["group_version"],
                        "agent_version": versions[1],
                    },
                )
            )
            db.add(
                ModelCall(
                    request_id=observed_request.id,
                    user_id=run["created_by"],
                    role="TechnicalManager",
                    model=model,
                    status="Settled",
                    reserved_micros=0,
                    actual_micros=1,
                    price_version=price_for(model).version,
                    request_fingerprint="synthetic-observation",
                    output={"reply": "Synthetic observation"},
                )
            )
        target = MemberGroup(name=f"Expansion-{uuid4()}", configuration=deepcopy(DEFAULT_CONFIGURATION))
        db.add(target)
        db.flush()
        target_id = target.id
    monitor_canaries()
    with transaction() as db:
        assert db.get(ChangeRequest, release["id"]).status == "Observed"
    expanded = owner_client.post(
        "/api/owner/releases",
        json=dict(
            group_id=target_id,
            expected_group_version=1,
            evaluation_id=run["id"],
            source_release_id=release["id"],
            reason="Synthetic observed canary expansion.",
        ),
    )
    assert expanded.status_code == 201, expanded.text
    expanded = expanded.json()
    ep = f"/api/owner/releases/{expanded['id']}"
    assert (
        owner_client.post(
            ep + "/review", json=dict(decision="Approved", fingerprint=expanded["fingerprint"])
        ).status_code
        == 200
    )
    assert owner_client.post(ep + "/execute").json()["status"] == "Expanded"
    with transaction() as db:
        db.add(
            WorkItem(
                request_id=observed_request.id,
                role="TechnicalManager",
                subject_kind="Conversation",
                subject_id=str(uuid4()),
                status="Waiting",
                wait_reason="TechnicalReview",
                input_versions={
                    "group_id": group["id"],
                    "group": change.execution["group_version"],
                    "agent_version": versions[1],
                },
            )
        )
    monitor_canaries()
    with transaction() as db:
        assert db.get(ChangeRequest, release["id"]).status == "RolledBack"
        assert (
            db.get(MemberGroup, group["id"]).configuration.get("agent_versions", {}).get("TechnicalManager")
            != versions[1]
        )
    assert (
        owner_client.post(
            ep + "/rollback", json={"reason": "End isolated protocol verification."}
        ).status_code
        == 200
    )
