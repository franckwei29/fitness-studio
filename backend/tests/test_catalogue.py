from decimal import Decimal

from app.catalogue import food_catalogue, food_provenance


def test_real_food_catalogue_matches_manifest_and_preserves_preparation():
    foods = {k: v for k, v in food_catalogue().items() if v.catalogue_version == "ausnut-2023-full-v2"}
    assert len(foods) == food_provenance()["record_count"] == 3741
    assert foods["F007661"].name == "Rice, white, cooked"
    assert foods["F006143"].name == "Oats, rolled, uncooked"
    assert "Milk" in foods["F005614"].allergens
    assert "Gluten" in foods["F006143"].allergens
    assert all(food.energy_kcal_per_100g >= Decimal(0) for food in foods.values())
    assert "Food Standards Australia New Zealand" in food_provenance()["attribution"]


def test_exercise_catalogue_requires_explicit_equipment():
    from app.catalogue import exercise_catalogue

    exercises = {k: v for k, v in exercise_catalogue().items() if v.catalogue_version == "studio-expanded-v2"}
    assert len(exercises) == 99
    assert exercises["sit-to-stand"].equipment == ["Stable chair"]
    assert exercises["wall-press-up"].equipment == ["Wall"]
    assert exercises["biceps-curl"].equipment == ["Dumbbells"]
    assert all(item.instructions and item.source_url for item in exercises.values())
