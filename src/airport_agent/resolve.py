"""Turn what a user typed ("LA", "Santa Ana", "boston") into a covered airport code."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from airport_agent.data.catalog import OurAirportsCatalog

CODE = re.compile(r"[a-z]{3}")


class Match(StrEnum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    NOT_COVERED = "not_covered"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Resolution:
    status: Match
    code: str | None
    alternatives: tuple[str, ...]  # other readings, or the candidates when ambiguous
    note: str  # what to tell the user when we assumed a reading or need to ask


def _words(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).split()


def _alias(query: str, codes: Sequence[str], catalog: OurAirportsCatalog) -> Resolution:
    code, others = codes[0], tuple(codes[1:])
    airport = catalog.get(code)
    name = airport.name if airport else code
    note = f'"{query}" read as {code} ({name})'

    if others:
        note += f"; other readings: {', '.join(others)}"

    return Resolution(Match.RESOLVED, code, others, note)


def _by_name(query: str, words: list[str], catalog: OurAirportsCatalog) -> Resolution:
    hits = sorted(a.iata for a in catalog.all() if set(words) <= set(_words(a.name)))

    if len(hits) == 1:
        airport = catalog.get(hits[0])
        name = airport.name if airport else hits[0]

        return Resolution(Match.RESOLVED, hits[0], (), f'"{query}" read as {hits[0]} ({name})')

    if hits:
        return Resolution(
            Match.AMBIGUOUS, None, tuple(hits), f'"{query}" could be {", ".join(hits)}: ask which'
        )

    return Resolution(Match.UNKNOWN, None, (), f'no covered airport matches "{query}"')


def resolve_airport(
    query: str, catalog: OurAirportsCatalog, aliases: Mapping[str, Sequence[str]]
) -> Resolution:
    words = _words(query)
    text = " ".join(words)

    if not words:
        return Resolution(Match.UNKNOWN, None, (), "no airport given")

    if text in aliases:
        return _alias(query, aliases[text], catalog)

    if CODE.fullmatch(text):
        code = text.upper()

        if catalog.get(code):
            return Resolution(Match.RESOLVED, code, (), "")

        return Resolution(Match.NOT_COVERED, None, (), f"{code} is not in my coverage list")

    return _by_name(query, words, catalog)
