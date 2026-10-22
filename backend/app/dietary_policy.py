"""Explicit supported dietary constraints; unknown restrictions require review."""

from app.policy import PolicyDenied

ALLERGENS = {
    "milk": "Milk",
    "dairy": "Milk",
    "乳制品": "Milk",
    "牛奶": "Milk",
    "egg": "Egg",
    "eggs": "Egg",
    "鸡蛋": "Egg",
    "gluten": "Gluten",
    "麸质": "Gluten",
    "oats": "Oats",
    "燕麦": "Oats",
}
TAGS = {"vegan": "Vegan", "纯素": "Vegan", "vegetarian": "Vegetarian", "素食": "Vegetarian"}


def dietary_constraints(values):
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise PolicyDenied("Confirm dietary restrictions before nutrition planning.")
    allergens, tags = set(), set()
    for value in values:
        normalized = value.strip().casefold()
        if normalized in ALLERGENS:
            allergens.add(ALLERGENS[normalized])
        elif normalized in TAGS:
            tags.add(TAGS[normalized])
        else:
            raise PolicyDenied("A dietary restriction needs clarification or catalogue review.")
    return sorted(allergens), sorted(tags)
