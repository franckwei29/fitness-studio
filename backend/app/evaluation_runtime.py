"""One bounded evaluation case per durable task, using the shared model gateway."""

from time import monotonic

from sqlalchemy import select

from app.admission import fingerprint
from app.database import transaction
from app.evaluation_records import verify_evaluation_snapshot
from app.evaluation_suite import output_contract, score_case
from app.model_gateway import generate
from app.models import AgentVersion, EvaluationResult, EvaluationRun, ModelCall, now
from app.policy import execution_context
from app.pricing import price_for
from app.work_queue import event, lock_claim


def run_evaluation(claim):
    with transaction() as db:
        item, request = lock_claim(db, claim)
        execution_context(db, item, request)
        run = db.get(EvaluationRun, item.subject_id)
        version = db.get(AgentVersion, request.payload["agent_version_id"])
        case = next(case for case in run.cases if case["key"] == request.payload["case_key"])
        instruction, role, version_id = version.prompt, run.role, version.id
        context = case["input"] | {"agent_version_id": version_id}
    started = monotonic()
    output, call_id = generate(claim, instruction, context, output_contract(role), 4096)
    elapsed = int((monotonic() - started) * 1000)
    result = score_case(role, case, output.model_dump(mode="json"))
    with transaction() as db:
        item, request = lock_claim(db, claim)
        execution_context(db, item, request)
        run = db.scalar(select(EvaluationRun).where(EvaluationRun.id == item.subject_id).with_for_update())
        verify_evaluation_snapshot(db, run)
        call = db.get(ModelCall, call_id)
        pinned = db.get(AgentVersion, version_id)
        if (
            not call
            or call.request_id != request.id
            or call.status != "Settled"
            or call.model != pinned.model
            or call.price_version != price_for(pinned.model).version
            or call.output != output.model_dump(mode="json")
        ):
            raise ValueError("Evaluation requires a settled call belonging to this task.")
        previous = db.scalar(
            select(EvaluationResult).where(
                EvaluationResult.run_id == run.id,
                EvaluationResult.agent_version_id == version_id,
                EvaluationResult.case_key == case["key"],
            )
        )
        if not previous:
            db.add(
                EvaluationResult(
                    run_id=run.id,
                    agent_version_id=version_id,
                    case_key=case["key"],
                    input_fingerprint=fingerprint(case["input"]),
                    call_id=call_id,
                    output=output.model_dump(mode="json"),
                    checks=result,
                    passed=result["passed"],
                    latency_ms=elapsed,
                )
            )
            db.flush()
        item.status = request.status = "Completed"
        item.lease_until = None
        item.wait_reason = None
        request.result = {
            "evaluation_id": run.id,
            "case_key": case["key"],
            "passed": result["passed"],
            "call_id": call_id,
        }
        event(db, request, "evaluation_case_completed", **request.result)
        rows = list(db.scalars(select(EvaluationResult).where(EvaluationResult.run_id == run.id)))
        if len(rows) == len(run.cases) * 2:
            run.status = "Completed" if all(row.passed for row in rows) else "FailedChecks"
            run.completed_at = now()
            run.summary = (run.summary or {}) | {
                "completed_cases": len(rows),
                "passed_cases": sum(row.passed for row in rows),
                "manual_review_required": False,
                "release_eligible": all(row.passed for row in rows),
                "owner_approval_required": True,
            }
        return request.result
