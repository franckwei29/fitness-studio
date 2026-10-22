"""Evaluation evidence remains bound to original versions and input snapshots."""

from types import SimpleNamespace

import pytest

from app.admission import fingerprint
from app.evaluation_records import prepare_evaluation, verify_evaluation_snapshot
from app.policy import PolicyDenied
from app.settings import get_settings


class Database:
    def __init__(self):
        self.rows = {}
        for key in ("baseline", "candidate"):
            values = {"role": "Coach", "model": get_settings().gemini_model, "prompt": key}
            self.rows[key] = SimpleNamespace(id=key, fingerprint=fingerprint(values), **values)

    def get(self, model, key):
        return self.rows.get(key)

    def add(self, row):
        self.saved = row

    def flush(self):
        pass


def cases():
    return [{"key": "limited-time", "input": {"minutes": 30}, "expected": {"maximum_minutes": 30}}]


def test_input_copy_and_tamper_detection():
    db = Database()
    source = cases()
    run = prepare_evaluation(db, "owner", "baseline", "candidate", "suite-v1", source, "Fixture")
    assert run.status == "Prepared" and run.evidence_mode == "Fixture"
    assert run.summary is None
    source[0]["input"]["minutes"] = 60
    assert run.cases[0]["input"]["minutes"] == 30
    verify_evaluation_snapshot(db, run)
    run.cases[0]["input"]["minutes"] = 60
    with pytest.raises(PolicyDenied):
        verify_evaluation_snapshot(db, run)


def test_version_mutation_invalidates_comparison():
    db = Database()
    run = prepare_evaluation(db, "owner", "baseline", "candidate", "suite-v1", cases(), "Live")
    db.rows["candidate"].prompt = "changed"
    with pytest.raises(PolicyDenied):
        verify_evaluation_snapshot(db, run)


def test_duplicate_cases_and_ambiguous_evidence_rejected():
    db = Database()
    with pytest.raises(ValueError):
        prepare_evaluation(db, "owner", "baseline", "candidate", "suite-v1", cases() * 2, "Fixture")
    with pytest.raises(ValueError):
        prepare_evaluation(db, "owner", "baseline", "candidate", "suite-v1", cases(), "Unspecified")
    with pytest.raises(ValueError):
        prepare_evaluation(db, "owner", "baseline", "baseline", "suite-v1", cases(), "Live")
