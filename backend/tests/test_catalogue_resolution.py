"""Real DB catalogue checks; external responses are explicitly simulated."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from test_rules import facts as facts_fixture
from test_training import catalogue, proposal

from app import catalogue_resolver as resolver
from app.agent_contracts import IntakeFacts
from app.catalogue import plan_catalogue, save_external, search_catalogue, seed_catalogues
from app.database import transaction
from app.gemini import ProviderFailure
from app.model_gateway import ModelUnavailable
from app.models import FoodRecord
from app.rules import assess
from app.training import training_estimate

facts = facts_fixture


def food(name):
    return resolver.FoodLookup(
        id="placeholder",
        name=name,
        energy_kcal_per_100g=180,
        protein_g_per_100g=20,
        carbohydrate_g_per_100g=4,
        fat_g_per_100g=8,
        allergens=[],
        dietary_tags=[],
        dietary_status_known=False,
        preparation_state="Cooked",
        catalogue_version="placeholder",
    )


@pytest.mark.parametrize("weight", [None, 0, -1, float("inf"), float("nan"), True])
def test_confirmed_weight_must_be_real_positive(facts, weight):
    facts["weight_kg"] = weight
    assert assess(facts).outcome == "NeedsInformation"
    if weight is not None:
        with pytest.raises(ValidationError):
            IntakeFacts(weight_kg=weight)


def test_partial_intake_and_exercise_time_only():
    assert IntakeFacts().weight_kg is None
    estimate = training_estimate(proposal(), {"weight_kg": 80}, catalogue())
    assert estimate["kcal"] == 14  # 3.5 MET * 80 kg * 180 / 3600; no rest or warmup
    assert estimate["sessions"][0]["items"][0]["seconds"] == 180


def test_idempotent_seed_and_bilingual_local_hits(monkeypatch):
    seed_catalogues()
    with transaction() as db:
        before = db.scalar(select(func.count()).select_from(FoodRecord))
    seed_catalogues()
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(FoodRecord)) == before

    def forbidden(*args, **kwargs):
        raise AssertionError("Local hits must not dispatch a provider call")

    monkeypatch.setattr(resolver, "generate", forbidden)
    assert "beef" in resolver.resolve_food(None, "牛排").name.lower()
    assert resolver.resolve_exercise(None, "游泳").met > 0
    assert resolver.resolve_exercise(None, "篮球").category == "ball_sport"


def test_external_two_steps_then_cache_and_snapshot(monkeypatch):
    query = "Synthetic special food " + uuid4().hex
    calls = []

    def external(claim, instruction, context, output_type, *args, **kwargs):
        calls.append(kwargs)
        return (
            resolver.SearchEvidence(evidence="Synthetic evidence")
            if output_type is resolver.SearchEvidence
            else food(query)
        ), "synthetic"

    monkeypatch.setattr(resolver, "generate", external)
    entry = resolver.resolve_food(None, query)
    try:
        assert entry.origin == "Estimate" and entry.id.startswith("food-")
        assert len(calls) == 2 and calls[0]["web_search"]
        assert resolver.resolve_food(None, query).id == entry.id
        assert len(calls) == 2
        snapshot = {"catalogue_snapshot": {entry.id: entry.model_dump(mode="json")}}
        with transaction() as db:
            row = db.get(FoodRecord, entry.id)
            row.data = row.data | {"energy_kcal_per_100g": "999"}
        assert plan_catalogue(snapshot, "food")[entry.id].energy_kcal_per_100g == 180
    finally:
        with transaction() as db:
            db.execute(delete(FoodRecord).where(FoodRecord.id == entry.id))


def test_concurrent_cache_insertion_deduplicates():
    query = "Synthetic concurrent " + uuid4().hex
    data = food(query).model_dump(mode="json")
    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = list(pool.map(lambda _: save_external("food", query, data), range(6)))
    try:
        assert len({row.id for row in rows}) == 1
        assert len(search_catalogue("food", query)) == 1
    finally:
        with transaction() as db:
            db.execute(delete(FoodRecord).where(FoodRecord.id == rows[0].id))


def test_external_failure_retries_once_without_saving(monkeypatch):
    calls = []
    query = "Synthetic unavailable " + uuid4().hex

    def fail(*args, **kwargs):
        calls.append(1)
        error = ProviderFailure("Synthetic API failure")
        error.usage_settled = True
        raise error

    monkeypatch.setattr(resolver, "generate", fail)
    with pytest.raises(ModelUnavailable, match="could not retrieve"):
        resolver.resolve_food(None, query)
    assert len(calls) == 2 and not search_catalogue("food", query)


def test_budget_denial_does_not_retry_or_insert(monkeypatch):
    calls = []

    def denied(*args, **kwargs):
        calls.append(1)
        raise ModelUnavailable("Insufficient budget", "Budget")

    monkeypatch.setattr(resolver, "generate", denied)
    with pytest.raises(ModelUnavailable, match="budget"):
        resolver.resolve_exercise(None, "Synthetic sport " + uuid4().hex)
    assert len(calls) == 1


def test_missing_nutrients_and_unknown_dietary_data_not_zero_or_safe():
    data = food("Synthetic").model_dump()
    del data["protein_g_per_100g"]
    with pytest.raises(ValidationError):
        resolver.FoodLookup.model_validate(data)
    assert not resolver.compatible(food("Synthetic"), {"dietary_restrictions": ["milk"]})
    known = food("Milk").model_copy(update={"dietary_status_known": True, "allergens": ["Milk"]})
    assert not resolver.compatible(known, {"dietary_restrictions": ["milk"]})
