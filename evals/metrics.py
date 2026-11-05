"""Deterministic checks on recorded outcomes. Numbers here come from code, never from a judge."""

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from airport_agent.answer import Answer, Status
from airport_agent.tools.registry import ToolRegistry
from airport_agent.tools.result import ToolResult
from evals.cases import Case, Category, Disclosure, Expect, Turn

NO_TOOL = "none"
# A proxy must be called one in the model's own words, not only in the notes the code attaches.
PROXY_WORDING = re.compile(
    r"proxy|proxies|signal|indicator|cannot be observed|not (?:the|a) (?:share|measure)", re.I
)
PROXY_DISCLOSURES = (Disclosure.PROXY_LABEL, Disclosure.NOT_A_SHARE)
Headline = Callable[[dict[str, Any]], list[str]]
HEADLINES: dict[str, Headline] = {
    "airport_score": lambda d: [str(d["score"])],
    "rank_region": lambda d: [
        d["ranking"][0]["airport"]["iata"],
        str(d["ranking"][0]["score"]),
    ],
    "compare_congestion": lambda d: [a["delay_rate"]["shown"] for a in d["airports"]],
    "distance_profile": lambda d: [d["avg_distance"]["shown"]],
    "unmet_demand": lambda d: [d["signals"][0]["shown"]],
}


@dataclass(frozen=True)
class Outcome:
    case: Case
    turn: Turn
    answer: Answer
    model_calls: int
    seconds: float

    @property
    def called(self) -> list[str]:
        return [result.tool for result in self.answer.results]

    @property
    def label(self) -> str:
        return f"{self.case.id}: {self.turn.question!r}"


@dataclass(frozen=True)
class Summary:
    scores: dict[str, float]  # metric name -> 0..1
    confusion: Counter[tuple[str, str]]  # (expected first tool, called first tool) -> turns
    failures: list[str]
    turns: int
    model_calls: int
    seconds: float


def ratio(part: int, whole: int) -> float:
    return part / whole if whole else 1.0


def routing_scores(outcomes: list[Outcome]) -> tuple[float, float]:
    """Precision and recall of tool names, counted over every turn."""
    matched = called_total = expected_total = 0

    for outcome in outcomes:
        expected = Counter(call.tool for call in outcome.turn.calls)
        called = Counter(outcome.called)
        matched += sum((expected & called).values())
        called_total += sum(called.values())
        expected_total += sum(expected.values())

    return ratio(matched, called_total), ratio(matched, expected_total)


def confusion_matrix(outcomes: list[Outcome]) -> Counter[tuple[str, str]]:
    def first(tools: list[str]) -> str:
        return tools[0] if tools else NO_TOOL

    return Counter((first([call.tool for call in o.turn.calls]), first(o.called)) for o in outcomes)


def is_abstention(answer: Answer) -> bool:
    return not any(result.ok for result in answer.results)


def states(disclosure: Disclosure, answer: Answer) -> bool:
    notes = answer.notes
    checks: dict[Disclosure, Callable[[], bool]] = {
        Disclosure.DATA_MONTH: lambda: any("data through 20" in s for s in notes.sources),
        Disclosure.DATA_MODE: lambda: any(
            ": live," in s or ": snapshot," in s for s in notes.sources
        ),
        Disclosure.PROXY_LABEL: lambda: any(PROXY_WORDING.search(c) for c in notes.caveats),
        Disclosure.ASSUMPTIONS: lambda: bool(notes.assumptions),
        Disclosure.ALIAS_READING: lambda: any("read as" in a for a in notes.assumptions),
        Disclosure.NOT_A_SHARE: lambda: any("not the share" in c for c in notes.caveats),
    }

    return checks[disclosure]()


def missing_disclosures(outcome: Outcome) -> list[Disclosure]:
    return [d for d in outcome.turn.must_state if not states(d, outcome.answer)]


def has_proxy_wording(outcome: Outcome) -> bool:
    return PROXY_WORDING.search(outcome.answer.text) is not None


def gold_strings(turn: Turn, registry: ToolRegistry) -> list[str]:
    """The headline figures of the tools the case expects, run straight from the data."""
    results = [registry.call(call.tool, call.args) for call in turn.calls]

    return [text for result in results if result.ok for text in headline(result)]


def headline(result: ToolResult) -> list[str]:
    return HEADLINES[result.tool](result.data) if result.tool in HEADLINES else []


def quotes_all(answer: Answer, strings: list[str]) -> bool:
    return all(text.lower() in answer.text.lower() for text in strings)


def _answerable(outcomes: list[Outcome]) -> list[Outcome]:
    return [o for o in outcomes if o.turn.expect is Expect.ANSWER]


def _routing_failures(outcomes: list[Outcome]) -> list[str]:
    return [
        f"{o.label} expected tools {[c.tool for c in o.turn.calls]}, called {o.called}"
        for o in outcomes
        if Counter(c.tool for c in o.turn.calls) != Counter(o.called)
    ]


def _status_failures(outcomes: list[Outcome]) -> list[str]:
    answerable = _answerable(outcomes)
    declined = [o for o in outcomes if o.turn.expect is Expect.ABSTAIN]

    return [
        f"{o.label} expected an answer, got {o.answer.status.value} {list(o.answer.problems)}"
        for o in answerable
        if o.answer.status is not Status.ANSWERED
    ] + [
        f"{o.label} expected to abstain but a tool succeeded"
        for o in declined
        if not is_abstention(o.answer)
    ]


def _disclosure_failures(outcomes: list[Outcome]) -> list[str]:
    return [
        f"{o.label} did not state {[d.value for d in missing_disclosures(o)]}"
        for o in _answerable(outcomes)
        if missing_disclosures(o)
    ]


def summarize(outcomes: list[Outcome], registry: ToolRegistry) -> Summary:
    answerable = _answerable(outcomes)
    declined = [o for o in outcomes if o.turn.expect is Expect.ABSTAIN]
    with_disclosures = [o for o in answerable if o.turn.must_state]
    with_proxy = [
        o
        for o in answerable
        if o.answer.status is Status.ANSWERED
        and any(d in PROXY_DISCLOSURES for d in o.turn.must_state)
    ]
    gold = [(o, gold_strings(o.turn, registry)) for o in answerable if o.turn.calls]
    gold = [(o, strings) for o, strings in gold if strings]
    precision, recall = routing_scores(outcomes)
    scores = {
        "routing_precision": precision,
        "routing_recall": recall,
        "answered_rate": ratio(
            sum(o.answer.status is Status.ANSWERED for o in answerable), len(answerable)
        ),
        "abstention": ratio(sum(is_abstention(o.answer) for o in declined), len(declined)),
        "disclosures": ratio(
            sum(not missing_disclosures(o) for o in with_disclosures), len(with_disclosures)
        ),
        "gold_match": ratio(sum(quotes_all(o.answer, s) for o, s in gold), len(gold)),
        "proxy_wording": ratio(sum(has_proxy_wording(o) for o in with_proxy), len(with_proxy)),
    }
    failures = [
        *_routing_failures(outcomes),
        *_status_failures(outcomes),
        *_disclosure_failures(outcomes),
        *(f"{o.label} did not quote {s}" for o, s in gold if not quotes_all(o.answer, s)),
    ]

    return Summary(
        scores,
        confusion_matrix(outcomes),
        failures,
        len(outcomes),
        sum(o.model_calls for o in outcomes),
        sum(o.seconds for o in outcomes),
    )


def by_category(outcomes: list[Outcome]) -> dict[Category, int]:
    return dict(Counter(o.case.category for o in outcomes))
