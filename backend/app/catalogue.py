"""Versioned food data with explicit source provenance."""

import json
from functools import lru_cache
from pathlib import Path

from app.nutrition import FoodDefinition

DATA = Path(__file__).parent / "data"


@lru_cache
def food_catalogue():
    rows = json.loads((DATA / "foods.json").read_text())
    foods = [FoodDefinition.model_validate(row) for row in rows]
    result = {food.id: food for food in foods}
    manifest = json.loads((DATA / "foods.manifest.json").read_text())
    if len(result) != len(foods) or len(result) != manifest["record_count"]:
        raise ValueError("The food catalogue does not match its manifest.")
    if any(food.catalogue_version != manifest["version"] for food in foods):
        raise ValueError("Food catalogue versions are inconsistent.")
    return result


def food_provenance():
    return json.loads((DATA / "foods.manifest.json").read_text())


@lru_cache
def exercise_catalogue():
    from app.training import ExerciseDefinition

    rows = json.loads((DATA / "exercises.json").read_text())
    exercises = [ExerciseDefinition.model_validate(row) for row in rows]
    result = {exercise.id: exercise for exercise in exercises}
    manifest = json.loads((DATA / "exercises.manifest.json").read_text())
    if len(result) != len(exercises) or len(result) != manifest["record_count"]:
        raise ValueError("The exercise catalogue does not match its manifest.")
    if any(exercise.catalogue_version != manifest["version"] for exercise in exercises):
        raise ValueError("Exercise catalogue versions are inconsistent.")
    return result
