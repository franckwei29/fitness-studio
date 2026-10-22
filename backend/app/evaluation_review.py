"""Reconstruct evaluation evidence before accepting an owner's quality review."""

from sqlalchemy import select

from app.admission import fingerprint
from app.evaluation_records import verify_evaluation_snapshot
from app.evaluation_suite import output_contract, score_case
from app.gemini import build_payload
from app.models import EvaluationResult, ModelCall, Request, WorkItem
from app.policy import PolicyDenied
from app.pricing import price_for


def verified_evidence(db, run):
    baseline, candidate = verify_evaluation_snapshot(db, run)
    if run.evidence_mode != "Live":
        raise PolicyDenied("Fixture results cannot authorize a model release.")
    versions = {row.id: row for row in (baseline, candidate)}
    cases = {case["key"]: case for case in run.cases}
    expected = {(version_id, key) for version_id in versions for key in cases}
    rows = list(db.scalars(select(EvaluationResult).where(EvaluationResult.run_id == run.id)))
    if len(rows) != len(expected) or {(row.agent_version_id, row.case_key) for row in rows} != expected:
        raise PolicyDenied("Every baseline and candidate case must have exactly one completed result.")
    request_ids = (run.summary or {}).get("request_ids", [])
    evidence = []
    used_calls = set()
    for row in rows:
        version, case = versions[row.agent_version_id], cases[row.case_key]
        call = db.get(ModelCall, row.call_id) if row.call_id else None
        if not call or call.id in used_calls or call.request_id not in request_ids:
            raise PolicyDenied("Each result must reference its own registered provider call.")
        used_calls.add(call.id)
        request = db.get(Request, call.request_id)
        item = db.scalar(select(WorkItem).where(WorkItem.request_id == call.request_id))
        if (
            not request
            or not item
            or request.kind != "Evaluation"
            or request.user_id != run.created_by
            or request.status != "Completed"
            or item.status != "Completed"
            or item.subject_id != run.id
            or item.role != run.role
            or request.payload.get("agent_version_id") != version.id
            or request.payload.get("case_key") != row.case_key
        ):
            raise PolicyDenied("Evaluation task provenance is incomplete or inconsistent.")
        price = price_for(version.model)
        context = case["input"] | {"agent_version_id": version.id}
        payload = build_payload(version.model, version.prompt, context, output_contract(run.role), 4096)
        digest = fingerprint({"work_id": item.id, "payload": payload})
        checks = score_case(run.role, case, row.output)
        if (
            call.status != "Settled"
            or call.model != version.model
            or call.price_version != price.version
            or call.request_fingerprint != digest
            or call.output != row.output
            or row.input_fingerprint != fingerprint(case["input"])
            or call.actual_micros != price.actual(call.usage or {})
            or not checks["passed"]
            or row.checks != checks
            or not row.passed
        ):
            raise PolicyDenied("Evaluation evidence does not pass the current integrity and rule checks.")
        evidence.append(
            {
                "result_id": row.id,
                "call_id": call.id,
                "version_id": version.id,
                "case_key": row.case_key,
                "input_fingerprint": row.input_fingerprint,
                "output": row.output,
                "checks": checks,
                "usage": call.usage,
                "actual_micros": call.actual_micros,
            }
        )
    return fingerprint(
        {
            "run_id": run.id,
            "suite": run.suite_fingerprint,
            "results": sorted(evidence, key=lambda item: item["result_id"]),
        }
    ), rows
