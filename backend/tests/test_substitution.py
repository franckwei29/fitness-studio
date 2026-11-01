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


def test_multiple_selected_portions_change_together_without_touching_others():
    baseline = original()
    revised = deepcopy(baseline)
    revised['meals'][0]['portions'][1] = {'food_id':'banana','grams':'80'}
    revised['meals'][1]['portions'][0] = {'food_id':'oats','grams':'60'}
    selection = {'selections':[{'meal_index':0,'portion_index':1},{'meal_index':1,'portion_index':0}]}
    assert check_substitution(NutritionProposal.model_validate(revised), baseline, selection) == []
    revised['meals'][0]['portions'][0]['grams'] = '70'
    assert check_substitution(NutritionProposal.model_validate(revised), baseline, selection)


@pytest.mark.parametrize("replacement", ["brown-rice", "banana", "beef", "fish", "potato"])
def test_assembly_preserves_unselected_text_and_portions(replacement):
    from app.substitution import assemble_substitution
    baseline = original()
    proposed = deepcopy(baseline)
    proposed["meals"][0]["portions"][0] = {"food_id": replacement, "grams": "180"}
    proposed["meals"][0]["portions"][1]["grams"] = "300"
    proposed["meals"][1]["preparation"] = "Model paraphrased dinner."
    selection = {"meal_index": 0, "portion_index": 0}
    assembled = assemble_substitution(NutritionProposal.model_validate(proposed), baseline, selection)
    after = assembled.model_dump(mode="json")
    assert after["meals"][1] == baseline["meals"][1]
    assert after["meals"][0]["portions"][1] == baseline["meals"][0]["portions"][1]
    assert after["meals"][0]["portions"][0]["food_id"] == replacement
    assert not check_substitution(assembled, baseline, selection)
    assert baseline == original()


def test_assembly_does_not_disguise_noop_or_reordered_meals():
    from app.substitution import assemble_substitution
    baseline = original()
    selection = {"meal_index": 0, "portion_index": 0}
    for content in (baseline, baseline | {"meals": list(reversed(baseline["meals"]))}):
        result = assemble_substitution(NutritionProposal.model_validate(content), baseline, selection)
        assert check_substitution(result, baseline, selection)



def test_numerically_identical_selected_portion_is_not_a_replacement():
    baseline = original()
    content = deepcopy(baseline)
    content["meals"][0]["portions"][0]["grams"] = "50.0"
    assert check_substitution(NutritionProposal.model_validate(content), baseline,
                              {"meal_index": 0, "portion_index": 0}) == ["A selected portion was not changed."]


def test_assembled_multiple_targets_still_run_dietary_and_catalogue_checks():
    from app.substitution import assemble_substitution
    from app.nutrition import FoodDefinition, validate_nutrition
    baseline = original()
    content = deepcopy(baseline)
    content["meals"][0]["portions"][1] = {"food_id":"milk", "grams":"150"}
    content["meals"][1]["portions"][0] = {"food_id":"unknown", "grams":"180"}
    selection = {"selections":[{"meal_index":0,"portion_index":1},{"meal_index":1,"portion_index":0}]}
    assembled = assemble_substitution(NutritionProposal.model_validate(content),baseline,selection)
    assert not check_substitution(assembled,baseline,selection)
    catalogue = {key: FoodDefinition(id=key,name=key,energy_kcal_per_100g=50,
        protein_g_per_100g=5,carbohydrate_g_per_100g=5,fat_g_per_100g=1,
        allergens=["milk"] if key=="milk" else [],dietary_tags=[],source_url="https://example.test",catalogue_version="synthetic")
        for key in ["oats","milk"]}
    result=validate_nutrition(assembled,catalogue,"training",["milk"],[])
    assert not result["passed"]
    assert "Excluded allergen in milk." in result["violations"]
    assert "Unknown food catalogue entry: unknown." in result["violations"]
