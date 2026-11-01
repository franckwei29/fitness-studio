"""Database-backed catalogues, idempotent seeds and small local search results."""

import gzip
import hashlib
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert

from app.database import transaction
from app.models import ExerciseRecord, FoodRecord
from app.nutrition import FoodDefinition

DATA = Path(__file__).parent / "data"
DEFAULT_FOOD_IDS = [
    "F000114",
    "F000262",
    "F001900",
    "F002275",
    "F002592",
    "F003721",
    "F005177",
    "F005614",
    "F006143",
    "F007661",
]
DEFAULT_EXERCISE_IDS = [
    "sit-to-stand",
    "mini-squat",
    "biceps-curl",
    "wall-press-up",
    "bodyweight-squat",
    "glute-bridge",
    "outdoor-walking",
    "pool-swimming",
    "basketball",
]
# Search translations, not additional nutrient facts. Specific states remain part of the query.
TERMS = {
    "牛排": "beef steak",
    "牛肉": "beef",
    "鸡胸肉": "chicken breast",
    "鸡肉": "chicken",
    "猪肉": "pork",
    "三文鱼": "salmon",
    "鸡蛋": "egg",
    "牛奶": "milk",
    "米饭": "rice cooked",
    "燕麦": "oats",
    "苹果": "apple",
    "香蕉": "banana",
    "西兰花": "broccoli",
    "胡萝卜": "carrot",
    "扁豆": "lentil",
    "豆腐": "tofu",
    "面包": "bread",
    "土豆": "potato",
    "酸奶": "yoghurt",
    "奶酪": "cheese",
    "虾": "prawn",
    "意大利面": "pasta",
}


def normalize(value):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).casefold()).strip()


def key_for(value):
    return hashlib.sha256(normalize(value).encode()).hexdigest()


def definition_type(kind):
    from app.training import ExerciseDefinition

    return FoodDefinition if kind == "food" else ExerciseDefinition


def record_type(kind):
    if kind not in ("food", "exercise"):
        raise ValueError("Unknown catalogue kind.")
    return FoodRecord if kind == "food" else ExerciseRecord


def record_values(data, lookup=None):
    return dict(
        id=data["id"],
        lookup_key=key_for(lookup or data["name"]),
        search_text=normalize(" ".join([data["name"], *data.get("aliases", [])])),
        data=data,
    )


@lru_cache
def seeds():
    foods = json.loads(gzip.decompress((DATA / "foods.seed.json.gz").read_bytes()))
    exercises = json.loads((DATA / "exercises.seed.json").read_text())
    return foods, exercises


def seed_catalogues():
    with transaction() as db:
        for kind, entries in zip(("food", "exercise"), seeds(), strict=True):
            table = record_type(kind)
            existing = set(db.scalars(select(table.id)))
            missing = [
                record_values(definition_type(kind).model_validate(row).model_dump(mode="json"))
                for row in entries
                if row["id"] not in existing
            ]
            for start in range(0, len(missing), 250):
                stmt = insert(table).values(missing[start : start + 250])
                db.execute(stmt.on_duplicate_key_update(id=table.id))


def catalogue(kind, ids=None):
    table = record_type(kind)
    with transaction() as db:
        stmt = select(table)
        if ids is not None:
            stmt = stmt.where(table.id.in_(ids))
        return {r.id: definition_type(kind).model_validate(r.data) for r in db.scalars(stmt)}


def food_catalogue(ids=None):
    return catalogue("food", ids)


def exercise_catalogue(ids=None):
    return catalogue("exercise", ids)


def food_provenance():
    return json.loads((DATA / "foods.seed.manifest.json").read_text())


def search_catalogue(kind, query, limit=8):
    table = record_type(kind)
    query = normalize(query)
    if not query:
        return []
    with transaction() as db:
        exact = db.scalar(select(table).where(table.lookup_key == key_for(query)))
        if exact:
            return [definition_type(kind).model_validate(exact.data)]
        for zh, en in TERMS.items():
            query = query.replace(zh, en)
        words = re.findall(r"[\w-]+", query)
        if not words:
            return []
        stmt = select(table)
        for word in words:
            stmt = stmt.where(table.search_text.contains(word, autoescape=True))
        records = list(db.scalars(stmt.order_by(table.id).limit(100)))
        records.sort(key=lambda r: (len(r.data["name"]), r.id))
        return [definition_type(kind).model_validate(r.data) for r in records[:limit]]


def save_external(kind, query, data):
    table = record_type(kind)
    digest = key_for(query)
    values = dict(
        data,
        id=("food-" if kind == "food" else "exercise-") + digest[:32],
        catalogue_version="external-v1",
        origin="Estimate",
    )
    values["aliases"] = list(dict.fromkeys([query, *values.get("aliases", [])]))
    parsed = definition_type(kind).model_validate(values)
    with transaction() as db:
        stmt = insert(table).values(**record_values(parsed.model_dump(mode="json"), query))
        db.execute(stmt.on_duplicate_key_update(id=table.id))
        row = db.scalar(select(table).where(table.lookup_key == digest))
        return definition_type(kind).model_validate(row.data)


def plan_catalogue(content, kind):
    """Saved snapshots, not mutable cache entries, determine saved plan values."""
    snapshot = content.get("catalogue_snapshot")
    if snapshot is not None:
        return {k: definition_type(kind).model_validate(v) for k, v in snapshot.items()}
    ids = (
        {p["food_id"] for m in content.get("meals", []) for p in m["portions"]}
        if kind == "food"
        else {x["exercise_id"] for s in content.get("sessions", []) for x in s["exercises"]}
    )
    return catalogue(kind, ids)


def proposal_content(content):
    return {
        k: v
        for k, v in content.items()
        if k not in ("catalogue_snapshot", "training_estimate", "nutrient_totals")
    }


def catalogue_labels(contents):
    return {
        row.id: row.name
        for content in contents
        for row in plan_catalogue(content, "food" if "meals" in content else "exercise").values()
    }
