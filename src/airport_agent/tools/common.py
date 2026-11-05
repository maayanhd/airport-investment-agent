"""Pieces every tool needs: airport resolution and the standard notes about the data."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from airport_agent.dataset import Dataset
from airport_agent.kpis import AirportKpis, compute_kpis
from airport_agent.resolve import Match, resolve_airport
from airport_agent.scoring import AirportScore, score_airport
from airport_agent.tools.result import ErrorKind, ToolResult

RATIO_DIGITS = 4  # decimals kept for fractions such as 0.2531
PROXY_CAVEAT = (
    "Delay pressure is a congestion proxy (share of operated flights 15+ minutes late, "
    "reporting carriers only), not a measurement of capacity or unmet demand."
)
ERROR_KINDS = {
    Match.AMBIGUOUS: ErrorKind.AMBIGUOUS_AIRPORT,
    Match.NOT_COVERED: ErrorKind.NOT_COVERED,
    Match.UNKNOWN: ErrorKind.UNKNOWN_AIRPORT,
}


@dataclass(frozen=True)
class Resolved:
    code: str
    assumptions: list[str]  # how the name was read, if it was not a code


def resolve(data: Dataset, tool: str, query: str) -> Resolved | ToolResult:
    """The airport code, or the failure to hand back so the agent asks or abstains."""
    found = resolve_airport(
        query,
        data.catalog,
        data.settings.airport_aliases,
        max_candidates=data.settings.max_name_candidates,
        volume=data.passenger_volume(),
    )

    if found.status is Match.RESOLVED and found.code:
        return Resolved(found.code, [found.note] if found.note else [])

    return ToolResult.failure(tool, ERROR_KINDS[found.status], found.note, list(found.alternatives))


def data_caveats(data: Dataset) -> list[str]:
    """What the reader must know about where the numbers came from."""
    notes = list(data.warnings)

    for source in data.sources():
        if source.note:  # set when live data fell back to a snapshot, or the data is stale
            heading = f"{source.source}: {source.mode.value}, as of {source.as_of or 'n/a'}"
            notes.append(f"{heading}. {source.note}".strip())

    return notes


def resolve_all(data: Dataset, tool: str, queries: list[str]) -> list[Resolved] | ToolResult:
    resolved = []

    for query in queries:
        found = resolve(data, tool, query)

        if not isinstance(found, Resolved):
            return found

        resolved.append(found)

    return resolved


def kpis_for(data: Dataset, code: str) -> AirportKpis:
    delays = data.delays.series(code) if data.delays else []

    return compute_kpis(
        code,
        data.traffic.series(code),
        delays,
        min_delay_months=data.settings.delays_min_months,
    )


def score_for(data: Dataset, kpis: AirportKpis) -> AirportScore:
    settings = data.settings

    return score_airport(
        kpis,
        weights=settings.score_weights,
        bounds=settings.score_bounds,
        min_confidence=settings.min_confidence,
    )


def airport_ref(data: Dataset, code: str) -> dict[str, str]:
    airport = data.catalog.get(code)

    return {
        "iata": code,
        "name": airport.name if airport else code,
        "state": airport.state if airport else "",
    }


def month(value: date | None) -> str | None:
    return f"{value:%Y-%m}" if value else None


def metric(
    value: float | None, show: Callable[[float], str], digits: int = RATIO_DIGITS
) -> dict[str, object] | None:
    """The number rounded once plus the text to quote, so the answer can match it exactly."""
    rounded = None if value is None else round(value, digits)

    return None if rounded is None else {"value": rounded, "shown": show(rounded)}


def _why_missing(name: str, code: str, data: Dataset) -> str:
    if name != "delay_pressure":
        return f"{name} needs consecutive months of traffic data"

    if data.delays is None:
        return "delay data is unavailable"

    months = len(data.delays.series(code))

    if months == 0:
        return "no On-Time rows, no reporting carrier serves it"

    return (
        f"fewer than {data.delays.min_months} consecutive months of delay data ({months} in total)"
    )


def partial_caveat(result: AirportScore, code: str, data: Dataset) -> list[str]:
    if not result.missing:
        return []

    reasons = "; ".join(_why_missing(name, code, data) for name in result.missing)

    return [
        f"{code}: built from {result.confidence:.0%} of the score weight ({reasons}). "
        "Not comparable with scores that have every component."
    ]
