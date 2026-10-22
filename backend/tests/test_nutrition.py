"""Synthetic nutrient data validates arithmetic, not dietary recommendations."""

import pytest

from app.nutrition import FoodDefinition, NutritionProposal, calculate_totals, validate_nutrition


def sample():
    food = FoodDefinition(
        id="synthetic-food",
        name="Synthetic food",
        energy_kcal_per_100g="200",
        protein_g_per_100g="10",
        carbohydrate_g_per_100g="20",
        fat_g_per_100g="5",
        allergens=["Milk"],
        dietary_tags=["Vegetarian"],
        source_url="https://example.test",
        catalogue_version="synthetic-v1",
    )
    plan = NutritionProposal.model_validate(
        {
            "title": "Test plan",
            "rationale": "Test arithmetic",
            "training_version_id": "training-1",
            "meals": [
                {
                    "name": "Test meal",
                    "portions": [{"food_id": food.id, "grams": "150"}],
                    "preparation": "Synthetic preparation",
                }
            ],
            "limitations": [],
        }
    )
    return plan, {food.id: food}


def test_nutrients_are_calculated_from_catalogue_and_grams():
    plan, catalogue = sample()
    assert calculate_totals(plan, catalogue) == {
        "energy_kcal": "300.00",
        "protein_g": "15.00",
        "carbohydrate_g": "30.00",
        "fat_g": "7.50",
    }


def test_allergens_preferences_and_training_version_are_checked():
    plan, catalogue = sample()
    assert validate_nutrition(plan, catalogue, "training-1", [], ["Vegetarian"])["passed"]
    assert not validate_nutrition(plan, catalogue, "training-1", ["milk"], [])["passed"]
    assert not validate_nutrition(plan, catalogue, "training-1", [], ["Vegan"])["passed"]
    assert not validate_nutrition(plan, catalogue, "training-2", [], [])["passed"]


def test_unknown_food_does_not_get_invented_nutrients():
    plan, _ = sample()
    with pytest.raises(ValueError, match="Unknown"):
        calculate_totals(plan, {})
    result = validate_nutrition(plan, {}, "training-1", [], [])
    assert not result["passed"]
    assert result["basis"]["totals"] is None
