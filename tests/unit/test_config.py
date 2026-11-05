import pytest
from pydantic import ValidationError

from airport_agent.config import Settings, load_settings


def test_default_config_loads() -> None:
    settings = load_settings()
    assert settings.long_haul_min_miles == 1500
    assert "MA" in settings.regions["new england"]


def test_score_weights_sum_to_one() -> None:
    weights = load_settings().score_weights
    assert sum(weights.values()) == pytest.approx(1.0)


def test_coverage_is_unique_iata_codes_including_required_airports() -> None:
    coverage = load_settings().coverage_airports
    assert {"ANC", "SFO", "LAX", "SNA", "BOS"} <= set(coverage)
    assert len(coverage) == len(set(coverage))


@pytest.mark.parametrize("bad", [["ANC", "ANC"], ["anc"], ["ANCH"], ["A1B"], ["ÄÖÜ"], [" BO"]])
def test_invalid_coverage_rejected(bad: list[str]) -> None:
    data = load_settings().model_dump()
    data["coverage_airports"] = bad
    with pytest.raises(ValueError, match="coverage_airports"):
        Settings.model_validate(data)


def test_history_window_must_cover_the_minimum_months() -> None:
    data = load_settings().model_dump()
    data["traffic_history_months"] = data["traffic_min_months"] - 1

    with pytest.raises(ValueError, match="traffic_history_months"):
        Settings.model_validate(data)


def test_delays_window_must_cover_the_minimum_months() -> None:
    data = load_settings().model_dump()
    data["delays_months"] = data["delays_min_months"] - 1

    with pytest.raises(ValueError, match="delays_months"):
        Settings.model_validate(data)


@pytest.mark.parametrize(
    ("weights", "message"),
    [
        ({"passenger_growth": 0.5, "capacity_headroom": 0.5}, "unknown score components"),
        ({"passenger_growth": 1.5, "load_factor": -0.5}, "must not be negative"),
    ],
)
def test_weights_must_name_known_components_and_not_be_negative(
    weights: dict[str, float], message: str
) -> None:
    bounds = {name: (0.0, 1.0) for name in weights}

    with pytest.raises(ValidationError, match=message):
        load_settings().model_validate(
            {**load_settings().model_dump(), "score_weights": weights, "score_bounds": bounds}
        )


@pytest.mark.parametrize(
    "aliases",
    [{"LA": ["LAX"]}, {"la": []}, {"la": ["XYZ"]}, {"la": ["LAX", "ZZZ"]}],
)
def test_aliases_must_be_lowercase_and_list_covered_airports(aliases: dict[str, list[str]]) -> None:
    data = load_settings().model_dump()
    data["airport_aliases"] = aliases

    with pytest.raises(ValueError, match="airport_aliases"):
        Settings.model_validate(data)


def test_alias_keys_must_be_in_the_form_the_resolver_matches() -> None:
    settings = load_settings()
    aliases = {**settings.airport_aliases, "st. louis": ["STL"]}

    with pytest.raises(ValidationError, match="airport_aliases"):
        Settings.model_validate({**settings.model_dump(), "airport_aliases": aliases})


@pytest.mark.parametrize(
    "regions",
    [
        {"New England": ["MA"]},
        {"new england": []},
        {"new england": ["Mass"]},
        {"new england": ["ma"]},
    ],
)
def test_regions_need_lower_case_names_and_two_letter_states(regions: dict[str, list[str]]) -> None:
    data = load_settings().model_dump()
    data["regions"] = regions

    with pytest.raises(ValidationError, match="region"):
        Settings.model_validate(data)
