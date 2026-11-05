"""Turn what a user typed ("LA", "Santa Ana", "chicago") into a covered airport code."""

import re
from collections.abc import Collection, Mapping, Sequence
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


def _has_words(airport: Airport, words: list[str]) -> bool:
    return set(words) <= set(search_words(airport.name)) | set(search_words(airport.city))


def _by_city(
    query: str,
    words: list[str],
    catalog: OurAirportsCatalog,
    volume: Mapping[str, int],
    max_candidates: int,
) -> Resolution | None:
    """Airports in the city the user named. Several in one city: the busiest is the default.
    Airports elsewhere whose name has the same words ("Manchester-Boston") are offered too."""
    group = [a for a in catalog.all() if search_words(a.city) == words]

    if not group:
        return None

    if len({a.state for a in group}) > 1:
        return _ask(query, group)

    ranked = sorted(group, key=lambda a: (-volume.get(a.iata, 0), a.iata))
    named = [a.iata for a in catalog.all() if a not in group and _has_words(a, words)]
    others = [a.iata for a in ranked[1:]] + named[:max_candidates]

    return _read_as(query, ranked[0].iata, others, catalog)


def _ask(query: str, airports: Sequence[Airport]) -> Resolution:
    codes = tuple(sorted(a.iata for a in airports))

    return Resolution(
        Match.AMBIGUOUS, None, codes, f'"{query}" could be {", ".join(codes)}: ask which'
    )


def _by_name(
    query: str, words: list[str], catalog: OurAirportsCatalog, max_candidates: int
) -> Resolution:
    hits = [a for a in catalog.all() if _has_words(a, words)]

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

    text = " ".join(words)

    if CODE.fullmatch(text):
        code = text.upper()

        if catalog.get(code):
            return Resolution(Match.RESOLVED, code, (), "")

        return Resolution(Match.NOT_COVERED, None, (), f"{code} is not in my coverage list")

    return _by_city(query, words, catalog, volume, max_candidates) or _by_name(
        query, words, catalog, max_candidates
    )


def without_readings(
    found: Resolution, query: str, exclude: Collection[str], catalog: OurAirportsCatalog
) -> Resolution:
    """Drop airports the user named elsewhere from the other readings: when asked to compare LA
    with Santa Ana, SNA is not an alternative reading of LA."""
    others = [code for code in found.alternatives if code not in exclude]

    if (
        found.status is not Match.RESOLVED
        or found.code is None
        or len(others) == len(found.alternatives)
    ):
        return found

    return _read_as(query, found.code, others, catalog)
