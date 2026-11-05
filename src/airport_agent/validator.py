"""Checks an answer against the tool results it was written from. Pure: no I/O, no model.

The rules: every number and every covered airport in the answer must appear in a tool result (or
in the question); a number written with a sign, or after a rise or fall word, must have that sign
in the tools; in a sentence about an airport, a number must be one of its own or a shared one; and
a rank or "A is more delayed than B" claim must match what a tool decided. Dates, years in a year
position, ordinals and plain counts are exempt.
What it cannot check: claims in words the patterns do not recognise ("clearly ahead"), a number
that two airports happen to share, and which of two airports in one sentence a number belongs to.
"""

import re
from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from airport_agent.claims import claim_problems, direction_problem
from airport_agent.config import Settings
from airport_agent.domain import search_words
from airport_agent.evidence import CODE, Evidence
from airport_agent.numbers import NUMBER_WORDS, Figure, find_figures

YEAR_POSITION = re.compile(r"\b(?:in|since|from|through|until|by|during|of|to|for)\s$", re.I)
SENTENCES = re.compile(r"(?<=[.!?])\s+|\n+")


class ViolationKind(StrEnum):
    NUMBER = "number"
    AIRPORT = "airport"
    CLAIM = "claim"
    EMPTY = "empty"


@dataclass(frozen=True)
class Violation:
    kind: ViolationKind
    text: str  # as written in the answer


@dataclass(frozen=True)
class Rules:
    coverage: Collection[str]
    max_plain_count: int
    years: tuple[int, int]
    city_codes: Mapping[str, frozenset[str]] = field(default_factory=dict)  # "boston" -> {"BOS"}

    @classmethod
    def from_settings(
        cls, settings: Settings, city_codes: Mapping[str, frozenset[str]] | None = None
    ) -> "Rules":
        return cls(
            settings.coverage_airports,
            settings.max_plain_count,
            settings.year_range,
            city_codes or {},
        )


def _is_exempt(figure: Figure, rules: Rules) -> bool:
    if figure.unit or figure.has_separator:
        return False

    in_year_position = YEAR_POSITION.search(figure.before) is not None
    low, high = rules.years

    return 0 <= figure.value <= rules.max_plain_count or (
        in_year_position and low <= figure.value <= high
    )


def _owner_problem(figure: Figure, codes: list[str], evidence: Evidence) -> str | None:
    """A number in a sentence about one airport must not be only another airport's."""
    own = [evidence.owned[c] for c in codes if c in evidence.owned]

    if not own or figure.value in evidence.shared or any(figure.value in values for values in own):
        return None

    return f"{figure.text} (not a figure for {', '.join(dict.fromkeys(codes))})"


def _figure_problem(
    figure: Figure, codes: list[str], evidence: Evidence, rules: Rules
) -> str | None:
    if figure.value not in evidence.numbers:
        return None if _is_exempt(figure, rules) else figure.text

    if _is_exempt(figure, rules):
        return None

    return direction_problem(figure, evidence) or _owner_problem(figure, codes, evidence)


def _sentence_codes(sentence: str, rules: Rules) -> list[str]:
    return [c for c in CODE.findall(sentence) if c in rules.coverage]


def _sentence_violations(sentence: str, evidence: Evidence, rules: Rules) -> Iterator[Violation]:
    codes = _sentence_codes(sentence, rules)
    problems = (_figure_problem(f, codes, evidence, rules) for f in find_figures(sentence))

    yield from (Violation(ViolationKind.NUMBER, p) for p in problems if p)
    yield from (
        Violation(ViolationKind.CLAIM, p) for p in claim_problems(sentence, codes, evidence)
    )


def _number_word_violations(answer: str, evidence: Evidence) -> Iterator[Violation]:
    for match in NUMBER_WORDS.finditer(answer):
        if match.group(0).lower() not in evidence.words:
            yield Violation(ViolationKind.NUMBER, match.group(0))


def _city_violations(answer: str, evidence: Evidence, rules: Rules) -> Iterator[Violation]:
    """A covered city named in words is an airport mention too, so a tool must have returned it."""
    text = f" {' '.join(search_words(answer))} "

    for city, codes in rules.city_codes.items():
        phrase = f" {city} "
        is_known = phrase in evidence.question or bool(codes & evidence.codes)

        if phrase in text and not is_known:
            yield Violation(ViolationKind.AIRPORT, city)


def validate_answer(answer: str, evidence: Evidence, rules: Rules) -> list[Violation]:
    """Numbers, number words, airports and claims that no tool result or question supports."""
    sentences = (s for s in SENTENCES.split(answer) if s.strip())
    numbers = [v for sentence in sentences for v in _sentence_violations(sentence, evidence, rules)]
    codes = sorted({c for c in CODE.findall(answer) if c in rules.coverage} - evidence.codes)

    return [
        *numbers,
        *_number_word_violations(answer, evidence),
        *(Violation(ViolationKind.AIRPORT, code) for code in codes),
        *_city_violations(answer, evidence, rules),
    ]
