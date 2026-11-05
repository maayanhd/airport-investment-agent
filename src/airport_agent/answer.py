"""What the agent hands back: the text plus notes written by code, never by the model."""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from airport_agent.tools.result import ToolResult


class Status(StrEnum):
    ANSWERED = "answered"
    ABSTAINED = "abstained"  # no answer could be verified against the tool results
    UNAVAILABLE = "unavailable"  # the model could not be reached


@dataclass(frozen=True)
class Notes:
    """Assumptions, caveats and data sources of the tools used, so every answer states them."""

    assumptions: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class Answer:
    status: Status
    text: str
    notes: Notes = Notes()
    results: tuple[ToolResult, ...] = ()
    problems: tuple[str, ...] = ()  # what the validator rejected, for logs and evals


def _unique(lines: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(lines))


def build_notes(results: Iterable[ToolResult]) -> Notes:
    used = list(results)

    return Notes(
        assumptions=_unique(a for r in used for a in r.assumptions),
        caveats=_unique(c for r in used for c in r.caveats),
        sources=_unique(
            f"{s.source}: {s.mode.value}, data through {s.as_of or 'n/a'}"
            for r in used
            for s in r.sources
        ),
    )
