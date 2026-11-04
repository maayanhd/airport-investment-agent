"""KPIs from monthly traffic and delays. Pure functions: no I/O, no clock, no config."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import TypeVar

from airport_agent.domain import MonthlyDelays, MonthlyTraffic

YEAR = 12
Row = TypeVar("Row", MonthlyTraffic, MonthlyDelays)


@dataclass(frozen=True)
class AirportKpis:
    """A value is None when the data cannot support it (too few or non-consecutive months)."""

    iata: str
    traffic_through: date | None
    delays_through: date | None
    passenger_growth: float | None  # last 12 months vs the 12 before, as a fraction
    seat_growth: float | None
    capacity_gap: float | None  # passenger growth minus seat growth
    load_factor: float | None  # percent of seats filled, last 12 months
    avg_distance_miles: float | None  # per departure, last 12 months
    delay_rate: float | None  # share of operated flights departing 15+ minutes late
    cancel_rate: float | None
    avg_delay_minutes: float | None  # per operated flight


def _index(month: date) -> int:
    return month.year * 12 + month.month


def _run(rows: Sequence[Row], count: int) -> list[Row]:
    """The newest run of consecutive months, at most `count` long."""
    ordered = sorted(rows, key=lambda r: r.month)
    run: list[Row] = []

    for row in reversed(ordered):
        if run and _index(run[0].month) - _index(row.month) != 1:
            break

        run.insert(0, row)

        if len(run) == count:
            break

    return run


def _div(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def _growth(window: list[MonthlyTraffic], value: Callable[[MonthlyTraffic], int]) -> float | None:
    before = sum(value(r) for r in window[:YEAR])
    after = sum(value(r) for r in window[YEAR:])
    ratio = _div(after, before)

    return None if ratio is None else ratio - 1


def _traffic_kpis(rows: Sequence[MonthlyTraffic]) -> dict[str, float | None]:
    two_years = _run(rows, 2 * YEAR)
    full = len(two_years) == 2 * YEAR
    year = two_years[YEAR:] if full else _run(rows, YEAR)
    has_year = len(year) == YEAR
    passengers = _growth(two_years, lambda r: r.passengers) if full else None
    seats = _growth(two_years, lambda r: r.seats) if full else None
    load = _div(100 * sum(r.passengers for r in year), sum(r.seats for r in year))
    distance = _div(
        sum(r.avg_flight_distance_miles * r.departures for r in year),
        sum(r.departures for r in year),
    )

    return {
        "passenger_growth": passengers,
        "seat_growth": seats,
        "capacity_gap": None if passengers is None or seats is None else passengers - seats,
        "load_factor": load if has_year else None,
        "avg_distance_miles": distance if has_year else None,
    }


def _delay_kpis(rows: Sequence[MonthlyDelays], min_months: int) -> dict[str, float | None]:
    window = _run(rows, YEAR)

    if len(window) < min_months:
        return {"delay_rate": None, "cancel_rate": None, "avg_delay_minutes": None}

    flights = sum(r.flights for r in window)
    cancelled = sum(r.cancelled for r in window)
    operated = flights - cancelled

    return {
        "delay_rate": _div(sum(r.delayed_15 for r in window), operated),
        "cancel_rate": _div(cancelled, flights),
        "avg_delay_minutes": _div(sum(r.delay_minutes for r in window), operated),
    }


def compute_kpis(
    iata: str,
    traffic: Sequence[MonthlyTraffic],
    delays: Sequence[MonthlyDelays],
    *,
    min_delay_months: int,
) -> AirportKpis:
    return AirportKpis(
        iata=iata,
        traffic_through=max((r.month for r in traffic), default=None),
        delays_through=max((r.month for r in delays), default=None),
        **_traffic_kpis(traffic),
        **_delay_kpis(delays, min_delay_months),
    )
