from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from airport_agent.config import Settings, load_settings
from airport_agent.data.delays import parse_delays
from airport_agent.data.traffic import parse_traffic
from airport_agent.kpis import AirportKpis, compute_kpis
from airport_agent.scoring import AirportScore, rank, scale, score_airport

SETTINGS = load_settings()
SNAPSHOTS = Path(__file__).parents[2] / "data" / "snapshots"
HIGH = {"passenger_growth": 0.10, "load_factor": 82.0, "delay_rate": 0.30, "capacity_gap": 0.05}
LOW = {"passenger_growth": -0.10, "load_factor": 50.0, "delay_rate": 0.15, "capacity_gap": -0.05}


def _kpis(values: Mapping[str, float | None] | None = None, iata: str = "AAA") -> AirportKpis:
    fields: dict[str, Any] = {
        "traffic_through": None,
        "delays_through": None,
        "seat_growth": None,
        "avg_distance_miles": None,
        "cancel_rate": None,
        "avg_delay_minutes": None,
        "passenger_growth": None,
        "load_factor": None,
        "delay_rate": None,
        "capacity_gap": None,
    }

    return AirportKpis(iata=iata, **{**fields, **(values or {})})


def _score(kpis: AirportKpis, settings: Settings = SETTINGS) -> AirportScore:
    return score_airport(
        kpis,
        weights=settings.score_weights,
        bounds=settings.score_bounds,
        min_confidence=settings.min_confidence,
    )


@pytest.mark.parametrize(
    ("value", "expected"), [(-1.0, 0.0), (0.0, 0.0), (5.0, 0.5), (10.0, 1.0), (99.0, 1.0)]
)
def test_scale_clips_to_the_range(value: float, expected: float) -> None:
    assert scale(value, 0.0, 10.0) == expected


def test_best_case_scores_100_and_worst_case_scores_0() -> None:
    assert _score(_kpis(HIGH)).score == pytest.approx(100.0)
    assert _score(_kpis(LOW)).score == pytest.approx(0.0)


def test_midpoint_of_every_range_scores_50() -> None:
    middle = {k: (HIGH[k] + LOW[k]) / 2 for k in HIGH}

    assert _score(_kpis(middle)).score == pytest.approx(50.0)


def test_points_add_up_to_the_score_and_follow_the_weights() -> None:
    result = _score(_kpis({**LOW, "passenger_growth": 0.10}))  # only growth is at its maximum

    assert result.score == pytest.approx(30.0)  # growth carries 0.3 of the weight
    assert sum(c.points for c in result.components) == pytest.approx(result.score or 0)
    assert result.confidence == pytest.approx(1.0)
    assert result.missing == ()


def test_a_missing_component_renormalizes_and_lowers_confidence() -> None:
    result = _score(_kpis({**HIGH, "delay_rate": None}))

    assert result.score == pytest.approx(100.0)  # the other three are all at their maximum
    assert result.confidence == pytest.approx(0.8)
    assert result.missing == ("delay_pressure",)


def test_too_little_data_gives_no_score_and_says_why() -> None:
    result = _score(_kpis({"passenger_growth": 0.05}))

    assert result.score is None
    assert result.confidence == pytest.approx(0.3)
    assert "30%" in result.reason
    assert set(result.missing) == {"load_factor", "delay_pressure", "capacity_gap"}


def test_confidence_exactly_at_the_minimum_is_accepted() -> None:
    at_minimum = SETTINGS.model_copy(update={"min_confidence": 0.6})
    result = _score(_kpis({"passenger_growth": 0.1, "load_factor": 85.0}), at_minimum)

    assert result.score == pytest.approx(100.0)


def test_unknown_component_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown score components"):
        score_airport(
            _kpis(HIGH),
            weights={"vibes": 1.0},
            bounds={"vibes": (0.0, 1.0)},
            min_confidence=0.5,
        )


def test_rank_puts_unscored_last_and_breaks_ties_by_code() -> None:
    tie_b = _score(_kpis(HIGH, "BBB"))
    tie_a = _score(_kpis(HIGH, "AAA"))
    weak = _score(_kpis(LOW, "CCC"))
    none = _score(_kpis(None, "DDD"))

    assert [s.iata for s in rank([none, weak, tie_b, tie_a])] == ["AAA", "BBB", "CCC", "DDD"]


@given(
    growth=st.floats(-1, 1),
    load=st.floats(0, 100),
    delay=st.floats(0, 1),
    gap=st.floats(-1, 1),
)
def test_score_stays_between_0_and_100(
    growth: float, load: float, delay: float, gap: float
) -> None:
    result = _score(
        _kpis(
            {
                "passenger_growth": growth,
                "load_factor": load,
                "delay_rate": delay,
                "capacity_gap": gap,
            }
        )
    )

    assert result.score is not None
    assert 0.0 <= result.score <= 100.0


@given(low=st.floats(-0.5, 0.5), step=st.floats(0, 0.5))
def test_more_growth_never_lowers_the_score(low: float, step: float) -> None:
    base = {"load_factor": 75.0, "delay_rate": 0.2, "capacity_gap": 0.0}
    worse = _score(_kpis({"passenger_growth": low, **base})).score
    better = _score(_kpis({"passenger_growth": low + step, **base})).score

    assert worse is not None
    assert better is not None
    assert better >= worse - 1e-9


def test_every_covered_airport_gets_an_honest_result_from_the_committed_snapshots() -> None:
    traffic = parse_traffic(
        (SNAPSHOTS / "bts_t100_traffic.json").read_text(),
        SETTINGS.coverage_airports,
        min_months=SETTINGS.traffic_min_months,
        min_airport_share=SETTINGS.min_airport_share,
    )
    delays = parse_delays(
        (SNAPSHOTS / "bts_ontime_delays.json").read_text(),
        SETTINGS.coverage_airports,
        min_months=SETTINGS.delays_min_months,
        min_airport_share=SETTINGS.min_airport_share,
    )
    scores = [
        _score(
            compute_kpis(
                code,
                traffic.get(code, []),
                delays.get(code, []),
                min_delay_months=SETTINGS.delays_min_months,
            )
        )
        for code in SETTINGS.coverage_airports
    ]
    values = [s.score for s in scores if s.score is not None]

    assert len(scores) == 97
    assert len(values) == 97  # delays are missing for 5, which still leaves 80% of the weight
    assert all(0.0 <= v <= 100.0 for v in values)
    assert sum(0.0 < v < 100.0 for v in values) >= 90  # the ranges do not saturate
    assert max(values) - min(values) > 30  # and they separate airports
    assert {s.iata for s in scores if s.missing} == {"HVN", "HYA", "ACK", "MVY", "LEB"}


def test_rank_puts_partial_scores_after_complete_ones() -> None:
    complete = _score(_kpis(LOW, "BBB"))
    partial = _score(_kpis({"passenger_growth": 0.10, "load_factor": 82.0}, "AAA"))

    assert partial.score is not None and complete.score is not None
    assert partial.score > complete.score
    assert [s.iata for s in rank([partial, complete])] == ["BBB", "AAA"]
