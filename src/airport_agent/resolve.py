"""Turn what a user typed ("LA", "Santa Ana", "chicago") into a covered airport code."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from airport_agent.data.catalog import OurAirportsCatalog
from airport_agent.domain import Airport, search_words

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


def _squash(text: str) -> str:
    return "".join(search_words(text))


def _read_as(
    query: str, code: str, others: Sequence[str], catalog: OurAirportsCatalog
) -> Resolution:
    airport = catalog.get(code)
    note = f'"{query}" read as {code} ({airport.name if airport else code})'

    if others:
        note += f"; other readings: {', '.join(others)}"

    return Resolution(Match.RESOLVED, code, tuple(others), note)


def _by_city(
    query: str, words: list[str], catalog: OurAirportsCatalog, volume: Mapping[str, int]
) -> Resolution | None:
    """Airports in the city the user named. Several in one city: the busiest is the default."""
    group = [a for a in catalog.all() if set(search_words(a.city)) == set(words)]

    if not group:
        return None

    if len({a.state for a in group}) > 1:
        return _ask(query, group)

    ranked = sorted(group, key=lambda a: (-volume.get(a.iata, 0), a.iata))

    return _read_as(query, ranked[0].iata, [a.iata for a in ranked[1:]], catalog)


def _ask(query: str, airports: Sequence[Airport]) -> Resolution:
    codes = tuple(sorted(a.iata for a in airports))

    return Resolution(
        Match.AMBIGUOUS, None, codes, f'"{query}" could be {", ".join(codes)}: ask which'
    )


def _by_name(
    query: str, words: list[str], catalog: OurAirportsCatalog, max_candidates: int
) -> Resolution:
    hits = [
        a
        for a in catalog.all()
        if set(words) <= set(search_words(a.name)) | set(search_words(a.city))
    ]

    if len(hits) > max_candidates:
        note = f'"{query}" matches {len(hits)} airports: give a city, a state or an airport code'

        return Resolution(Match.AMBIGUOUS, None, (), note)

    if len(hits) == 1:
        return _read_as(query, hits[0].iata, [], catalog)

    if hits:
        return _ask(query, hits)

    return Resolution(Match.UNKNOWN, None, (), f'no covered airport matches "{query}"')


def resolve_airport(
    query: str,
    catalog: OurAirportsCatalog,
    aliases: Mapping[str, Sequence[str]],
    *,
    max_candidates: int,
    volume: Mapping[str, int],
) -> Resolution:
    """`volume` is passengers per airport; it only decides the default inside one city."""
    words = search_words(query)

    if not words:
        return Resolution(Match.UNKNOWN, None, (), "no airport given")

    by_squashed = {_squash(alias): codes for alias, codes in aliases.items()}

    if _squash(query) in by_squashed:
        codes = by_squashed[_squash(query)]

        return _read_as(query, codes[0], codes[1:], catalog)

    if CODE.fullmatch(" ".join(words)):
        code = " ".join(words).upper()

        if catalog.get(code):
            return Resolution(Match.RESOLVED, code, (), "")

        return Resolution(Match.NOT_COVERED, None, (), f"{code} is not in my coverage list")

    return _by_city(query, words, catalog, volume) or _by_name(
        query, words, catalog, max_candidates
    )
