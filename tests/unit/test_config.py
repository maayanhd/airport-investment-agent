import pytest

from airport_agent.config import Settings, load_settings


def test_default_config_loads() -> None:
    settings = load_settings()
    assert settings.long_haul_min_miles == 1500
    assert "MA" in settings.new_england_states


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
