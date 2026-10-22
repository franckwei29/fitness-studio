import pytest

from app.dietary_policy import dietary_constraints
from app.policy import PolicyDenied


def test_supported_bilingual_constraints_are_explicit():
    assert dietary_constraints(["牛奶", "vegan"]) == (["Milk"], ["Vegan"])
    assert dietary_constraints([]) == ([], [])


@pytest.mark.parametrize("values", [None, ["unknown allergy"], ["lactose intolerance"], [False]])
def test_unresolved_restrictions_require_review(values):
    with pytest.raises(PolicyDenied):
        dietary_constraints(values)
