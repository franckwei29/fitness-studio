"""A food replacement cannot silently rewrite the rest of the meal plan."""

from copy import deepcopy

import pytest

from app.nutrition import NutritionProposal
from app.substitution import check_substitution


def original():
    return {
        "title": "Example",
        "rationale": "Example",
        "training_version_id": "training",
        "limitations": [],
        "meals": [
            {
                "name": "Breakfast",
                "preparation": "Prepare breakfast.",
                "portions": [{"food_id": "oats", "grams": "50"}, {"food_id": "apple", "grams": "100"}],
            },
            {
                "name": "Lunch",
                "preparation": "Prepare lunch.",
                "portions": [{"food_id": "rice", "grams": "150"}],
            },
        ],
    }


def test_selected_portion_and_its_preparation_can_change():
    baseline = original()
    revised = deepcopy(baseline)
    revised["meals"][0]["portions"][0] = {"food_id": "banana", "grams": "80"}
    revised["meals"][0]["preparation"] = "Prepare the replacement."
    assert not check_substitution(
        NutritionProposal.model_validate(revised), baseline, {"meal_index": 0, "portion_index": 0}
    )


@pytest.mark.parametrize("change", ["none", "another-meal", "another-portion", "extra-meal"])
def test_scope_violations_are_rejected(change):
    baseline = original()
    revised = deepcopy(baseline)
    if change != "none":
        revised["meals"][0]["portions"][0]["food_id"] = "banana"
    if change == "another-meal":
        revised["meals"][1]["preparation"] = "Changed"
    if change == "another-portion":
        revised["meals"][0]["portions"][1]["grams"] = "200"
    if change == "extra-meal":
        revised["meals"].append(deepcopy(revised["meals"][1]))
    assert check_substitution(
        NutritionProposal.model_validate(revised), baseline, {"meal_index": 0, "portion_index": 0}
    )
