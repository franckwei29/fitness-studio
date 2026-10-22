"""Catalogue-based nutrition totals; models never supply nutrient facts."""

from decimal import Decimal

from pydantic import Field

from app.agent_contracts import StrictModel

NUTRITION_RULE_VERSION = "nutrition-structure-v1"


class FoodDefinition(StrictModel):
    id: str
    name: str
    energy_kcal_per_100g: Decimal = Field(ge=0)
    protein_g_per_100g: Decimal = Field(ge=0, le=100)
    carbohydrate_g_per_100g: Decimal = Field(ge=0, le=100)
    fat_g_per_100g: Decimal = Field(ge=0, le=100)
    allergens: list[str]
    dietary_tags: list[str]
    source_url: str
    catalogue_version: str


class FoodPortion(StrictModel):
    food_id: str
    grams: Decimal = Field(gt=0, le=5000)


class Meal(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    portions: list[FoodPortion] = Field(min_length=1, max_length=30)
    preparation: str = Field(min_length=1, max_length=2000)


class NutritionProposal(StrictModel):
    title: str = Field(min_length=1, max_length=160)
    rationale: str = Field(min_length=1, max_length=3000)
    training_version_id: str | None
    meals: list[Meal] = Field(min_length=1, max_length=10)
    limitations: list[str]


def calculate_totals(proposal, catalogue):
    totals = {key: Decimal(0) for key in ("energy_kcal", "protein_g", "carbohydrate_g", "fat_g")}
    for meal in proposal.meals:
        for portion in meal.portions:
            food = catalogue.get(portion.food_id)
            if food is None:
                raise ValueError(f"Unknown food catalogue entry: {portion.food_id}.")
            for key in totals:
                totals[key] += getattr(food, f"{key}_per_100g") * portion.grams / 100
    return {key: str(value.quantize(Decimal("0.01"))) for key, value in totals.items()}


def validate_nutrition(proposal, catalogue, training_id, excluded_allergens, required_tags):
    violations = []
    if proposal.training_version_id != training_id:
        violations.append("Nutrition does not reference the exact training version.")
    excluded = {item.casefold().strip() for item in excluded_allergens}
    tags = {item.casefold().strip() for item in required_tags}
    for meal in proposal.meals:
        for portion in meal.portions:
            food = catalogue.get(portion.food_id)
            if food is None:
                violations.append(f"Unknown food catalogue entry: {portion.food_id}.")
                continue
            if excluded & {item.casefold().strip() for item in food.allergens}:
                violations.append(f"Excluded allergen in {food.name}.")
            if not tags <= {item.casefold().strip() for item in food.dietary_tags}:
                violations.append(f"Dietary requirements are not satisfied by {food.name}.")
            if not food.source_url:
                violations.append(f"Food provenance is missing: {food.id}.")
    totals = (
        None
        if any(portion.food_id not in catalogue for meal in proposal.meals for portion in meal.portions)
        else calculate_totals(proposal, catalogue)
    )
    return {
        "passed": not violations,
        "violations": violations,
        "rule_version": NUTRITION_RULE_VERSION,
        "basis": {
            "training_id": training_id,
            "totals": totals,
            "catalogue_versions": sorted({food.catalogue_version for food in catalogue.values()}),
        },
    }
