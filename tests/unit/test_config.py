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


def _changed(**updates: object) -> dict[str, object]:
    return {**load_settings().model_dump(), **updates}


def test_scoring_weights_and_bounds_must_name_the_same_components() -> None:
    settings = load_settings()
    bounds = {k: v for k, v in settings.score_bounds.items() if k != "load_factor"}

    with pytest.raises(ValidationError, match="same components"):
        Settings.model_validate(_changed(score_bounds=bounds))


def test_score_weights_must_sum_to_one_in_the_model() -> None:
    weights = {k: 0.3 for k in load_settings().score_weights}

    with pytest.raises(ValidationError, match="sum to 1"):
        Settings.model_validate(_changed(score_weights=weights))


def test_each_score_range_needs_a_low_below_the_high() -> None:
    bounds = {**load_settings().score_bounds, "load_factor": (82.0, 50.0)}

    with pytest.raises(ValidationError, match="low < high"):
        Settings.model_validate(_changed(score_bounds=bounds))


@pytest.mark.parametrize("value", [0, 1.5, -0.1])
def test_strong_signal_position_must_be_inside_0_and_1(value: float) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate(_changed(strong_signal_scaled=value))


@pytest.mark.parametrize("years", [(2099, 1990), (2000, 2000)])
def test_the_year_range_must_have_a_low_below_the_high(years: tuple[int, int]) -> None:
    with pytest.raises(ValidationError, match="year_range"):
        Settings.model_validate(_changed(year_range=years))


def test_the_request_budget_covers_every_call_timing_out_on_every_attempt() -> None:
    settings = load_settings().model_copy(
        update={
            "max_tool_rounds": 4,
            "validation_retries": 1,
            "llm_timeout_seconds": 30.0,
            "llm_retries": 1,
        }
    )

    assert settings.request_budget_seconds == (4 + 1 + 1) * 30 * 2
