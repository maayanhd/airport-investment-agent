"""Expansion score: fixed ranges scale each KPI to 0..1, a weighted sum gives 0..100.

Pure functions. The ranges are fixed, not relative to the airports compared, so an airport's
score does not change when the question or the comparison set changes.
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from airport_agent.kpis import AirportKpis

CONFIDENCE_TOLERANCE = 1e-9
COMPONENTS: dict[str, Callable[[AirportKpis], float | None]] = {
    "passenger_growth": lambda k: k.passenger_growth,
    "load_factor": lambda k: k.load_factor,
    "delay_pressure": lambda k: k.delay_rate,
    "capacity_gap": lambda k: k.capacity_gap,
}


@dataclass(frozen=True)
class Component:
    name: str
    value: float
    scaled: float  # 0..1 after clipping to the configured range
    weight: float
    points: float  # share of the 0..100 score this component contributed


@dataclass(frozen=True)
class AirportScore:
    iata: str
    score: float | None  # None when there is not enough data to stand behind a number
    confidence: float  # total weight of the components that could be computed
    components: tuple[Component, ...]
    missing: tuple[str, ...]
    reason: str  # why there is no score, or ""


def scale(value: float, low: float, high: float) -> float:
    return min(1.0, max(0.0, (value - low) / (high - low)))


def _component(
    name: str, value: float, weight: float, bounds: tuple[float, float], total_weight: float
) -> Component:
    scaled = scale(value, *bounds)

    return Component(name, value, scaled, weight, 100 * weight * scaled / total_weight)


def score_airport(
    kpis: AirportKpis,
    *,
    weights: Mapping[str, float],
    bounds: Mapping[str, tuple[float, float]],
    min_confidence: float,
) -> AirportScore:
    unknown = weights.keys() - COMPONENTS.keys()

    if unknown:
        raise ValueError(f"unknown score components: {sorted(unknown)}")

    values = {name: COMPONENTS[name](kpis) for name in weights}
    available = {name: value for name, value in values.items() if value is not None}
    missing = tuple(name for name, value in values.items() if value is None)
    confidence = sum(weights[name] for name in available)

    if not available or confidence + CONFIDENCE_TOLERANCE < min_confidence:
        reason = f"only {confidence:.0%} of the score weight has data (need {min_confidence:.0%})"

        return AirportScore(kpis.iata, None, confidence, (), missing, reason)

    components = tuple(
        _component(name, value, weights[name], bounds[name], confidence)
        for name, value in available.items()
    )

    return AirportScore(
        kpis.iata, sum(c.points for c in components), confidence, components, missing, ""
    )


def rank(scores: Iterable[AirportScore]) -> list[AirportScore]:
    """Best first. Airports with every component come before partial ones (a missing component
    rescales the weights, so its score is not comparable); unscored go last; ties break by code."""
    return sorted(
        scores, key=lambda s: (s.score is None, bool(s.missing), -(s.score or 0.0), s.iata)
    )
