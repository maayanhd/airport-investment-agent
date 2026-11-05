"""How score components are named and shown. One place, so the CLI and the tools agree."""

from collections.abc import Callable

from airport_agent.config import Settings

LABELS = {
    "passenger_growth": "Passenger growth (last 12 months vs the 12 before)",
    "load_factor": "Load factor (share of seats filled, last 12 months)",
    "delay_pressure": "Delay pressure (share of operated flights 15+ min late; a congestion proxy)",
    "capacity_gap": "Capacity gap (passenger growth minus seat growth, in percentage points)",
}
FORMATS: dict[str, Callable[[float], str]] = {
    "passenger_growth": lambda v: f"{v:+.1%}",
    "load_factor": lambda v: f"{v:.1f}%",
    "delay_pressure": lambda v: f"{v:.1%}",
    "capacity_gap": lambda v: f"{100 * v:+.1f} pts",
}


def describe_scoring(settings: Settings) -> list[str]:
    """The weights and ranges behind a score, as sentences the user can be told."""
    return [
        f"{LABELS[name]}: weight {weight:.2f}, "
        f"{FORMATS[name](settings.score_bounds[name][0])} scores 0 and "
        f"{FORMATS[name](settings.score_bounds[name][1])} scores 1"
        for name, weight in settings.score_weights.items()
    ]
