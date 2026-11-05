"""What an answer may draw on: figures, airports and orderings in tool results and the question."""

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from airport_agent.domain import IATA_PATTERN, search_words
from airport_agent.numbers import NUMBER_WORDS, find_figures, to_decimal
from airport_agent.tools.result import ToolResult

CODE = re.compile(r"\b[A-Z]{3}\b")
PERCENT = Decimal(100)


@dataclass(frozen=True)
class Evidence:
    numbers: frozenset[Decimal]  # magnitudes of every figure that may be quoted
    positive: frozenset[Decimal]  # of those, the ones seen without a minus sign
    negative: frozenset[Decimal]
    shared: frozenset[Decimal]  # figures that belong to no single airport
    owned: Mapping[str, frozenset[Decimal]]  # airport code -> its figures
    words: frozenset[str]  # number words such as "half" that a result or the question used
    codes: frozenset[str]
    rankings: tuple[Mapping[str, int], ...]  # one per tool result: airport code -> its rank
    delay_order: frozenset[tuple[str, str]]  # (more delayed, less delayed) as a tool decided
    question: str  # the question as padded lower-case words, for phrase lookups
    scores: tuple[Mapping[str, float], ...] = ()  # like rankings, but the score itself
    text: str = ""  # every string a tool returned as padded lower-case words, for city lookups


@dataclass
class _Found:
    positive: set[Decimal] = field(default_factory=set)
    negative: set[Decimal] = field(default_factory=set)
    shared: set[Decimal] = field(default_factory=set)
    owned: dict[str, set[Decimal]] = field(default_factory=dict)
    words: set[str] = field(default_factory=set)
    codes: set[str] = field(default_factory=set)
    rankings: list[dict[str, int]] = field(default_factory=list)
    scores: list[dict[str, float]] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    delay_order: set[tuple[str, str]] = field(default_factory=set)

    def add_number(self, value: Decimal, sign: int, owner: str | None) -> None:
        is_zero = value == 0  # zero has no sign; a sign nobody wrote (0) counts as positive

        if sign < 0 or is_zero:
            self.negative.add(value)

        if sign >= 0 or is_zero:
            self.positive.add(value)

        (self.owned.setdefault(owner, set()) if owner else self.shared).add(value)

    def add_text(self, text: str, owner: str | None, *, either_sign: bool = False) -> None:
        for figure in find_figures(text):
            self.add_number(figure.value, figure.sign, owner)

            if either_sign:
                self.add_number(figure.value, -1, owner)

        self.words |= {m.group(0).lower() for m in NUMBER_WORDS.finditer(text)}
        self.codes |= set(CODE.findall(text))


def _code(value: object) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(IATA_PATTERN, value) else None


def owner_of(node: Mapping[str, object]) -> str | None:
    """The airport a record is about: its own `iata`, or the one airport it refers to."""
    own = _code(node.get("iata"))

    if own:
        return own

    children = {_code(child.get("iata")) for child in node.values() if isinstance(child, dict)} - {
        None
    }

    return children.pop() if len(children) == 1 else None


def _walk(node: object, owner: str | None) -> Iterator[tuple[object, str | None]]:
    """Every dict, key and leaf, with the airport that owns it."""
    if isinstance(node, dict):
        owner = owner_of(node) or owner

        yield node, owner

        for key, value in node.items():
            yield key, owner
            yield from _walk(value, owner)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value, owner)
    else:
        yield node, owner


def airport_codes(node: object) -> Iterator[str]:
    """Every airport code a tool result is about, in the order it appears."""
    for item, _ in _walk(node, None):
        code = _code(item.get("iata")) if isinstance(item, dict) else None

        if code:
            yield code


def _quotable(value: float) -> Iterator[Decimal]:
    """A fraction may be quoted as a percent (0.85 as 85%)."""
    number = abs(to_decimal(str(value)))

    yield number

    if number <= 1:
        yield (number * PERCENT).normalize()


def _record_structure(node: dict[str, object], owner: str | None, found: _Found) -> None:
    rank = node.get("rank")

    if owner and isinstance(rank, int) and not isinstance(rank, bool):
        found.rankings[-1][owner] = rank

    score = node.get("score")

    if owner and isinstance(score, int | float) and not isinstance(score, bool):
        found.scores[-1][owner] = float(score)

    worse = _code(node.get("more_delayed"))
    rows = node.get("airports")
    rows = rows if isinstance(rows, list) else []
    others = {owner_of(row) for row in rows if isinstance(row, dict)} - {worse}
    other = others.pop() if len(others) == 1 else None

    if worse and other:
        found.delay_order.add((worse, other))


def _record(item: object, owner: str | None, found: _Found) -> None:
    if isinstance(item, dict):
        _record_structure(item, owner, found)
    elif isinstance(item, str):
        found.add_text(item, owner)
        found.texts.append(item)
    elif isinstance(item, int | float) and not isinstance(item, bool):
        sign = -1 if item < 0 else 1

        for magnitude in _quotable(item):
            found.add_number(magnitude, sign, owner)


def gather_evidence(results: Sequence[ToolResult], question: str = "") -> Evidence:
    """Provenance timestamps are left out: their digits would legitimise unrelated numbers."""
    found = _Found()
    dumps = [r.model_dump(mode="json", exclude={"sources"}) for r in results]

    for dump in dumps:
        found.rankings.append({})  # ranks are kept per tool result: two rankings can differ
        found.scores.append({})

        for item, owner in _walk(dump, None):
            _record(item, owner, found)

    found.add_text(question, None, either_sign=True)

    return Evidence(
        numbers=frozenset(found.positive | found.negative),
        positive=frozenset(found.positive),
        negative=frozenset(found.negative),
        shared=frozenset(found.shared),
        owned={code: frozenset(values) for code, values in found.owned.items()},
        words=frozenset(found.words),
        codes=frozenset(found.codes),
        rankings=tuple(r for r in found.rankings if r),
        delay_order=frozenset(found.delay_order),
        question=f" {' '.join(search_words(question))} ",
        scores=tuple(s for s in found.scores if s),
        text=f" {' '.join(search_words(' '.join(found.texts)))} ",
    )
