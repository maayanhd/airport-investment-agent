from datetime import date

import pytest

from airport_agent.domain import MonthlyDelays, MonthlyTraffic
from airport_agent.kpis import compute_kpis


def _month(offset: int) -> date:
    index = 2024 * 12 + offset

    return date(index // 12, index % 12 + 1, 1)


def _traffic(
    offsets: range,
    *,
    base: tuple[int, int] = (100, 200),
    departures: int = 10,
    miles: float = 500.0,
    later: tuple[int, int] | None = None,
) -> list[MonthlyTraffic]:
    """`base` is (passengers, seats) a month; `later` replaces it from the 13th month on."""
    rows = []

    for i in offsets:
        p, s = later if later and i >= 12 else base
        rows.append(
            MonthlyTraffic(
                iata="AAA",
                month=_month(i),
                departures=departures,
                passengers=p,
                seats=s,
                load_factor=min(100.0, 100 * p / s) if s else 0.0,
                avg_flight_distance_miles=miles,
            )
        )

    return rows


def _delays(offsets: range, *, flights: int = 100, cancelled: int = 2) -> list[MonthlyDelays]:
    return [
        MonthlyDelays(
            iata="AAA",
            month=_month(i),
            flights=flights,
            cancelled=cancelled,
            delayed_15=20,
            delay_minutes=500.0,
        )
        for i in offsets
    ]


def test_growth_and_load_factor_match_hand_calculation() -> None:
    # Year one 100 passengers and 200 seats a month; year two 110 passengers and 220 seats.
    k = compute_kpis("AAA", _traffic(range(24), later=(110, 220)), [], min_delay_months=9)

    assert k.passenger_growth == pytest.approx(0.10)
    assert k.seat_growth == pytest.approx(0.10)
    assert k.capacity_gap == pytest.approx(0.0)
    assert k.load_factor == pytest.approx(50.0)  # 110 / 220, from the newest 12 months only
    assert k.traffic_through == _month(23)


def test_capacity_gap_is_passenger_growth_minus_seat_growth() -> None:
    k = compute_kpis("AAA", _traffic(range(24), later=(120, 210)), [], min_delay_months=9)

    assert k.capacity_gap == pytest.approx(0.20 - 0.05)


def test_distance_is_weighted_by_departures() -> None:
    rows = _traffic(range(12), miles=100.0, departures=30) + _traffic(
        range(12, 24), miles=400.0, departures=10
    )
    k = compute_kpis("AAA", rows, [], min_delay_months=9)

    assert k.avg_distance_miles == pytest.approx(400.0)  # only the newest 12 months count


def test_year_over_year_needs_24_consecutive_months() -> None:
    k = compute_kpis("AAA", _traffic(range(18)), [], min_delay_months=9)

    assert k.passenger_growth is None
    assert k.capacity_gap is None
    assert k.load_factor == pytest.approx(50.0)  # 12 months are enough for the level


def test_a_missing_month_breaks_the_run() -> None:
    rows = [r for r in _traffic(range(24)) if r.month != _month(15)]
    k = compute_kpis("AAA", rows, [], min_delay_months=9)

    assert k.passenger_growth is None
    assert k.load_factor is None  # the newest consecutive run is only 8 months long


def test_zero_seats_and_zero_prior_year_give_none_not_an_error() -> None:
    k = compute_kpis("AAA", _traffic(range(24), base=(0, 0)), [], min_delay_months=9)

    assert (k.passenger_growth, k.load_factor) == (None, None)


def test_delay_rates_use_operated_flights() -> None:
    k = compute_kpis("AAA", [], _delays(range(12)), min_delay_months=9)

    assert k.delay_rate == pytest.approx(20 / 98)
    assert k.cancel_rate == pytest.approx(0.02)
    assert k.avg_delay_minutes == pytest.approx(500 / 98)
    assert k.delays_through == _month(11)


def test_delay_rates_need_the_minimum_consecutive_months() -> None:
    seasonal = _delays(range(0, 3)) + _delays(range(7, 12))  # newest run is 5 months

    k = compute_kpis("AAA", [], seasonal, min_delay_months=9)

    assert (k.delay_rate, k.cancel_rate, k.avg_delay_minutes) == (None, None, None)
    assert k.delays_through == _month(11)


def test_no_data_gives_all_none() -> None:
    k = compute_kpis("AAA", [], [], min_delay_months=9)

    assert k.traffic_through is None
    assert k.passenger_growth is None
    assert k.delay_rate is None
