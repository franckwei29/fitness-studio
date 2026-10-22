"""Capture reproducible evaluation inputs before any provider request is made."""

from copy import deepcopy

from app.admission import fingerprint
from app.agent_versions import verify_version
from app.models import AgentVersion, EvaluationRun
from app.policy import PolicyDenied


def prepare_evaluation(db, actor_id, baseline_id, candidate_id, suite_version, cases, evidence_mode):
    if evidence_mode not in {"Live", "Fixture"}:
        raise ValueError("Evaluation evidence must explicitly identify Live or Fixture mode.")
    if baseline_id == candidate_id:
        raise ValueError("Choose distinct baseline and candidate versions.")
    baseline = db.get(AgentVersion, baseline_id)
    candidate = db.get(AgentVersion, candidate_id)
    if not baseline or not candidate:
        raise PolicyDenied("Both comparison versions must exist.")
    verify_version(baseline, candidate.role)
    verify_version(candidate, baseline.role)
    if not suite_version or not cases:
        raise ValueError("A versioned nonempty evaluation suite is required.")
    keys = []
    for case in cases:
        if (
            not isinstance(case, dict)
            or not isinstance(case.get("key"), str)
            or not case["key"].strip()
            or len(case["key"]) > 80
            or not isinstance(case.get("input"), dict)
            or not isinstance(case.get("expected"), dict)
        ):
            raise ValueError("Each case needs a unique key, fixed input, and explicit expected checks.")
        keys.append(case["key"])
    if len(keys) != len(set(keys)):
        raise ValueError("Evaluation case keys must be unique.")
    snapshot = deepcopy(cases)
    row = EvaluationRun(
        created_by=actor_id,
        role=candidate.role,
        baseline_id=baseline.id,
        candidate_id=candidate.id,
        baseline_fingerprint=baseline.fingerprint,
        candidate_fingerprint=candidate.fingerprint,
        suite_version=suite_version,
        suite_fingerprint=fingerprint({"version": suite_version, "cases": snapshot}),
        cases=snapshot,
        evidence_mode=evidence_mode,
        status="Prepared",
    )
    db.add(row)
    db.flush()
    return row


def verify_evaluation_snapshot(db, run):
    baseline = verify_version(db.get(AgentVersion, run.baseline_id), run.role)
    candidate = verify_version(db.get(AgentVersion, run.candidate_id), run.role)
    if (
        baseline.fingerprint != run.baseline_fingerprint
        or candidate.fingerprint != run.candidate_fingerprint
        or fingerprint({"version": run.suite_version, "cases": run.cases}) != run.suite_fingerprint
    ):
        raise PolicyDenied("Evaluation inputs changed after preparation. Prepare a new comparison.")
    return baseline, candidate
