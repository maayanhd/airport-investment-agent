"""Checks an answer against the tool results it was written from. Pure: no I/O, no model.

The rule: every number and every covered airport code in the answer must appear in a tool result
(or in the question). Dates, years, ordinals and plain counts up to 10 are exempt.
"""

import re
from collections.abc import Collection, Iterator, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from airport_agent.tools.result import ToolResult

MAX_PLAIN_COUNT = 10  # "top 3", "2 of 4 signals"
YEARS = (1990, 2099)
DATES = re.compile(
    r"\b\d{4}-\d{2}(?:-\d{2})?\b"
    r"|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? \d{4}\b"
    r"|\b\d+(?:st|nd|rd|th)\b"
)
NUMBER = re.compile(
    r"(?<![\w.])(?P<number>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?P<unit>%|\s?(?:pts?|points|miles|minutes|min)\b)?"
)
CODE = re.compile(r"\b[A-Z]{3}\b")
WORD3 = re.compile(r"\b[A-Za-z]{3}\b")


class ViolationKind(StrEnum):
    NUMBER = "number"
    AIRPORT = "airport"


@dataclass(frozen=True)
class Violation:
    kind: ViolationKind
    text: str  # as written in the answer


def _strings(node: Any) -> Iterator[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)


def _leaves(node: Any) -> Iterator[int | float]:
    if isinstance(node, bool):
        return

    if isinstance(node, int | float):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _leaves(value)
    elif isinstance(node, list):
        for value in node:
            yield from _leaves(value)


def _decimal(text: str) -> Decimal:
    return Decimal(text.replace(",", "")).normalize()


def _numbers(text: str) -> Iterator[tuple[Decimal, re.Match[str]]]:
    for match in NUMBER.finditer(DATES.sub(" ", text)):
        yield _decimal(match["number"]), match


def _is_exempt(number: Decimal, match: re.Match[str]) -> bool:
    plain = not match["unit"] and not re.search(r"[.,]", match["number"])

    return plain and (0 <= number <= MAX_PLAIN_COUNT or YEARS[0] <= number <= YEARS[1])


@dataclass(frozen=True)
class Evidence:
    numbers: frozenset[Decimal]
    codes: frozenset[str]


def gather_evidence(results: Sequence[ToolResult], question: str = "") -> Evidence:
    """Provenance timestamps are left out: their digits would legitimise unrelated numbers."""
    dumped = [r.model_dump(mode="json", exclude={"sources"}) for r in results]
    texts = [text for d in dumped for text in _strings(d)]
    numbers = {_decimal(str(v)) for d in dumped for v in _leaves(d)}
    numbers |= {n for text in [*texts, question] for n, _ in _numbers(text)}
    codes = {c for text in texts for c in CODE.findall(text)}
    codes |= {w.upper() for w in WORD3.findall(question)}

    return Evidence(frozenset(numbers), frozenset(codes))


def validate_answer(
    answer: str, evidence: Evidence, *, coverage: Collection[str]
) -> list[Violation]:
    """Numbers and covered airport codes in `answer` that no tool result or question supports."""
    found = [
        Violation(ViolationKind.NUMBER, match.group(0).strip())
        for number, match in _numbers(answer)
        if number not in evidence.numbers and not _is_exempt(number, match)
    ]
    unknown = sorted({c for c in CODE.findall(answer) if c in coverage and c not in evidence.codes})

    return [*found, *(Violation(ViolationKind.AIRPORT, code) for code in unknown)]
