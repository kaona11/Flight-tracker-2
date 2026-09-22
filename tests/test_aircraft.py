import pytest

from qsuite.aircraft import is_qsuite, normalise, verdict_for_itinerary


@pytest.mark.parametrize("raw,code", [
    ("Boeing 777-300ER", "77W"),
    ("777-300ER", "77W"),
    ("A350-1000", "35K"),
    ("77W", "77W"),
])
def test_normalise_folds_marketing_names(raw, code):
    assert normalise(raw) == code


def test_qsuite_fleet_detected():
    verdict, reason = is_qsuite("77W")
    assert verdict is True
    assert "777-300ER" in reason


def test_non_qsuite_fleet_detected():
    verdict, _ = is_qsuite("788")
    assert verdict is False


def test_unknown_equipment_is_none_not_false():
    """Refusing to guess matters: False would claim a legacy seat we never saw."""
    verdict, reason = is_qsuite("ZZZ")
    assert verdict is None
    assert "unrecognised" in reason


def test_missing_equipment_is_none():
    assert is_qsuite(None)[0] is None
    assert is_qsuite("")[0] is None


def test_one_legacy_leg_downgrades_whole_itinerary():
    verdict, reason = verdict_for_itinerary(["77W", "788"])
    assert verdict is False
    assert "at least one leg" in reason


def test_all_qsuite_legs_confirm():
    assert verdict_for_itinerary(["77W", "35K"])[0] is True


def test_partial_knowledge_stays_unknown():
    assert verdict_for_itinerary(["77W", "ZZZ"])[0] is None
