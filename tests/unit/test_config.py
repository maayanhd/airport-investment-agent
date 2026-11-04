import pytest

from airport_agent.config import load_settings


def test_default_config_loads() -> None:
    settings = load_settings()
    assert settings.long_haul_min_miles == 1500
    assert "MA" in settings.new_england_states


def test_score_weights_sum_to_one() -> None:
    weights = load_settings().score_weights
    assert sum(weights.values()) == pytest.approx(1.0)
