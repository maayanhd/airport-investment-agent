"""How score components are named and shown. One place, so the CLI and the tools agree."""

from collections.abc import Callable

from airport_agent.config import Settings

LABELS = {
    "passenger_growth": "Passenger growth (last 12 months vs the 12 before)",
    "load_factor": "Load factor (share of seats filled, last 12 months)",
    "delay_pressure": "Delay pressure (share of operated flights 15+ min late; a congestion proxy)",
    "capacity_gap": "Capacity gap (passenger growth minus seat growth, in percentage points)",
}


def growth(value: float) -> str:
    return f"{value:+.1%}"


def percent(value: float) -> str:
    return f"{value:.1%}"


def load_percent(value: float) -> str:
    return f"{value:.1f}%"


def points(value: float) -> str:
    return f"{100 * value:+.1f} pts"


MILLION = 1_000_000


def passengers(value: float) -> str:
    return f"{value / MILLION:.1f} million" if value >= MILLION else f"{value:,.0f}"


def minutes(value: float) -> str:
    return f"{value:.1f} min"


def miles(value: float) -> str:
    return f"{value:,.0f} miles"


FORMATS: dict[str, Callable[[float], str]] = {
    "passenger_growth": growth,
    "load_factor": load_percent,
    "delay_pressure": percent,
    "capacity_gap": points,
}


SCORE_SCALE = "The expansion score runs from 0 to 100."


def describe_scoring(settings: Settings) -> list[str]:
    """The weights and ranges behind a score, as sentences the user can be told."""
    components = [
        f"{LABELS[name]}: weight {weight:.2f}, "
        f"{FORMATS[name](settings.score_bounds[name][0])} scores 0 and "
        f"{FORMATS[name](settings.score_bounds[name][1])} scores 1"
        for name, weight in settings.score_weights.items()
    ]

    return [SCORE_SCALE, *components]
