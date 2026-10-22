"""Release evidence gates validate provenance rather than trusting stored scores."""

from types import SimpleNamespace as Row

import pytest

from app.admission import fingerprint
from app.evaluation_review import verified_evidence
from app.evaluation_suite import evaluation_cases, output_contract, score_case
from app.gemini import build_payload
from app.models import AgentVersion, ModelCall, Request
from app.policy import PolicyDenied
from app.pricing import price_for
from app.settings import get_settings


def evidence():
    model = get_settings().gemini_model
    versions = []
    for identifier in ["baseline", "candidate"]:
        values = dict(role="TechnicalManager", model=model, prompt=identifier)
        versions.append(Row(id=identifier, fingerprint=fingerprint(values), **values))
    case = evaluation_cases("TechnicalManager")[0]
    run = Row(
        id="run",
        role="TechnicalManager",
        baseline_id="baseline",
        candidate_id="candidate",
        baseline_fingerprint=versions[0].fingerprint,
        candidate_fingerprint=versions[1].fingerprint,
        suite_version="test-suite",
        cases=[case],
        evidence_mode="Live",
        created_by="owner",
        summary={"request_ids": ["r0", "r1"]},
    )
    run.suite_fingerprint = fingerprint({"version": run.suite_version, "cases": run.cases})
    output = {"reply": "One call has unknown usage. Its cost has not been determined."}
    usage = {
        "total_input_tokens": 10,
        "total_output_tokens": 10,
        "total_thought_tokens": 0,
        "total_cached_tokens": 0,
        "total_tool_use_tokens": 0,
        "total_tokens": 20,
    }
    results, calls, requests, items = [], [], [], []
    for index, version in enumerate(versions):
        context = case["input"] | {"agent_version_id": version.id}
        payload = build_payload(model, version.prompt, context, output_contract(run.role), 4096)
        item = Row(id=f"w{index}", status="Completed", subject_id="run", role=run.role)
        items.append(item)
        calls.append(
            Row(
                id=f"c{index}",
                request_id=f"r{index}",
                status="Settled",
                model=model,
                price_version=price_for(model).version,
                output=output.copy(),
                usage=usage,
                actual_micros=price_for(model).actual(usage),
                request_fingerprint=fingerprint({"work_id": item.id, "payload": payload}),
            )
        )
        requests.append(
            Row(
                id=f"r{index}",
                kind="Evaluation",
                user_id="owner",
                status="Completed",
                payload={"agent_version_id": version.id, "case_key": case["key"]},
            )
        )
        results.append(
            Row(
                id=f"result{index}",
                agent_version_id=version.id,
                case_key=case["key"],
                call_id=f"c{index}",
                output=output.copy(),
                passed=True,
                checks=score_case(run.role, case, output),
                input_fingerprint=fingerprint(case["input"]),
            )
        )

    class Database:
        def get(self, table, identifier):
            collection = {AgentVersion: versions, ModelCall: calls, Request: requests}[table]
            return next(row for row in collection if row.id == identifier)

        def scalars(self, query):
            return results

        def scalar(self, query):
            return items.pop(0)

    return Database(), run, results, calls, requests


def test_complete_evidence_has_a_reproducible_fingerprint():
    db, run, results, _, _ = evidence()
    digest, rows = verified_evidence(db, run)
    assert len(digest) == 64 and rows == results


@pytest.mark.parametrize(
    "change", ["fixture", "missing", "output", "input", "cost", "request", "score", "call"]
)
def test_changed_or_incomplete_evidence_is_rejected(change):
    db, run, results, calls, requests = evidence()
    if change == "fixture":
        run.evidence_mode = "Fixture"
    elif change == "missing":
        results.pop()
    elif change == "output":
        results[0].output = {"reply": "Modified after generation."}
    elif change == "input":
        results[0].input_fingerprint = "changed"
    elif change == "cost":
        calls[0].actual_micros += 1
    elif change == "request":
        requests[0].payload["agent_version_id"] = "unrelated"
    elif change == "score":
        results[0].checks = {"passed": True}
    else:
        calls[0].request_fingerprint = "unrelated"
    with pytest.raises(PolicyDenied):
        verified_evidence(db, run)
