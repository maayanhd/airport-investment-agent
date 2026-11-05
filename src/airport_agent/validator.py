"""Checks an answer against the tool results it was written from. Pure: no I/O, no model.

The rule: every number and every covered airport code in the answer must appear in a tool result
(or in the question). Dates, years in a year position, ordinals and plain counts are exempt.
What it cannot check: which airport a number belongs to, and airports named without their code.
"""

import re
from collections.abc import Collection, Iterator, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from airport_agent.tools.result import ToolResult

DATES = re.compile(
    r"\b\d{4}-\d{2}(?:-\d{2})?\b"
    r"|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? \d{4}\b"
    r"|\b\d+(?:st|nd|rd|th)\b"
)
UNITS = (
    r"percentage points?|percent|per cent|pp|pts?|points?|miles?|minutes?|min|hours?|days?|"
    r"years?|million|billion|thousand"
)
NUMBER = re.compile(
    r"(?<![\w.])(?P<number>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    rf"(?P<unit>\s?%|\s?(?:{UNITS})\b|[MKB]\b)?"
)
NUMBER_WORDS = re.compile(
    r"(?<!\d )(?<!\d)\b(?:eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
    r"nineteen|twenty|"
    r"thirty|forty|fifty|sixty|seventy|eighty|ninety|dozens?|hundreds?|thousands?|millions?|"
    r"billions?|half|halves|quarters?|(?:a|one|two) thirds?|double[sd]?|doubling|triple[sd]?|"
    r"tripling|twice)\b|[\u00bd\u00bc\u00be\u2153\u2154]",
    re.IGNORECASE,
)
YEAR_POSITION = re.compile(r"\b(?:in|since|from|through|until|by|during|of|to|for)\s$", re.I)
CODE = re.compile(r"\b[A-Z]{3}\b")


class ViolationKind(StrEnum):
    NUMBER = "number"
    AIRPORT = "airport"
    EMPTY = "empty"


@dataclass(frozen=True)
class Violation:
    kind: ViolationKind
    text: str  # as written in the answer


@dataclass(frozen=True)
class Evidence:
    numbers: frozenset[Decimal]
    words: frozenset[str]  # number words such as "half" that a result or the question used
    codes: frozenset[str]


def _walk(node: object) -> Iterator[object]:
    """Every key and leaf of nested dicts and lists."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)
    else:
        yield node


def _decimal(text: str) -> Decimal:
    return Decimal(text.replace(",", "")).normalize()


def _numbers(text: str) -> Iterator[tuple[Decimal, re.Match[str]]]:
    for match in NUMBER.finditer(DATES.sub(" ", text)):
        yield _decimal(match["number"]), match


def _quotable(value: float) -> Iterator[Decimal]:
    """Signs are ignored, and a fraction may be quoted as a percent (0.85 as 85%)."""
    number = abs(_decimal(str(value)))

    yield number

    if number <= 1:
        yield (number * 100).normalize()


def gather_evidence(results: Sequence[ToolResult], question: str = "") -> Evidence:
    """Provenance timestamps are left out: their digits would legitimise unrelated numbers."""
    leaves = list(_walk([r.model_dump(mode="json", exclude={"sources"}) for r in results]))
    texts = [leaf for leaf in leaves if isinstance(leaf, str)]
    values = [
        leaf for leaf in leaves if isinstance(leaf, int | float) and not isinstance(leaf, bool)
    ]
    numbers = {n for value in values for n in _quotable(value)}
    numbers |= {n for text in [*texts, question] for n, _ in _numbers(text)}
    words = {m.group(0).lower() for text in [*texts, question] for m in NUMBER_WORDS.finditer(text)}
    codes = {c for text in [*texts, question] for c in CODE.findall(text)}

    return Evidence(frozenset(numbers), frozenset(words), frozenset(codes))


def _is_exempt(
    number: Decimal, match: re.Match[str], max_plain_count: int, years: tuple[int, int]
) -> bool:
    if match["unit"] or re.search(r"[.,]", match["number"]):
        return False

    in_year_position = YEAR_POSITION.search(match.string[: match.start()]) is not None

    return 0 <= number <= max_plain_count or (in_year_position and years[0] <= number <= years[1])


def validate_answer(
    answer: str,
    evidence: Evidence,
    *,
    coverage: Collection[str],
    max_plain_count: int,
    years: tuple[int, int],
) -> list[Violation]:
    """Numbers, number words and covered airport codes that no tool result or question supports."""
    numbers = [
        Violation(ViolationKind.NUMBER, match.group(0).strip())
        for number, match in _numbers(answer)
        if number not in evidence.numbers and not _is_exempt(number, match, max_plain_count, years)
    ]
    words = [
        Violation(ViolationKind.NUMBER, match.group(0))
        for match in NUMBER_WORDS.finditer(answer)
        if match.group(0).lower() not in evidence.words
    ]
    codes = sorted({c for c in CODE.findall(answer) if c in coverage and c not in evidence.codes})

    return [*numbers, *words, *(Violation(ViolationKind.AIRPORT, code) for code in codes)]
