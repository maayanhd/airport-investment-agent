"""Pieces every tool needs: airport resolution and the standard notes about the data."""

from dataclasses import dataclass

from airport_agent.dataset import Dataset
from airport_agent.resolve import Match, resolve_airport
from airport_agent.tools.result import ErrorKind, ToolResult

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
