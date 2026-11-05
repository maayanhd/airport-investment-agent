"""Deterministic checks on recorded outcomes. Numbers here come from code, never from a judge."""

import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from airport_agent.answer import Answer, Status
from airport_agent.domain import Provenance
from airport_agent.numbers import Figure, find_figures
from airport_agent.tools.common import PROXY_CAVEAT
from airport_agent.tools.distance_profile import NOT_A_SHARE
from airport_agent.tools.registry import ToolRegistry
from airport_agent.tools.result import ToolResult
from airport_agent.tools.unmet_demand import NOT_OBSERVED
from evals.cases import Case, Disclosure, Expect, Turn

NO_TOOL = "none"
# A proxy must be called one in the model's own words, not only in the notes the code attaches.
PROXY_WORDING = re.compile(
    r"proxy|proxies|signal|indicator|cannot be observed|not (?:the|a) (?:share|measure)", re.I
)
DECLINE_WORDING = re.compile(
    r"cannot|can't|could not|couldn't|unable|outside|not covered|not available|don't|do not|"
    r"no data|insufficient|not enough|which|please|specify|[?]",
    re.I,
)
PROXY_CAVEATS = frozenset({PROXY_CAVEAT, NOT_OBSERVED, NOT_A_SHARE})
PROXY_DISCLOSURES = (Disclosure.PROXY_LABEL, Disclosure.NOT_A_SHARE)
# ToolResult.data is untyped JSON by design (the model sees it as JSON), so a headline reads it
# defensively: a result that no longer has the expected shape fails one case, not the whole run.
Headline = Callable[[Mapping[str, Any]], list[str]]
SHAPE_ERRORS = (KeyError, IndexError, TypeError)


def _score(data: Mapping[str, Any]) -> list[str]:
    return [str(data["score"])]


def _ranking(data: Mapping[str, Any]) -> list[str]:
    top = data["ranking"][0]

    return [top["airport"]["iata"], str(top["score"])]


def _congestion(data: Mapping[str, Any]) -> list[str]:
    return [row["delay_rate"]["shown"] for row in data["airports"]]


def _distance(data: Mapping[str, Any]) -> list[str]:
    return [data["avg_distance"]["shown"]]


def _unmet(data: Mapping[str, Any]) -> list[str]:
    return [data["signals"][0]["shown"]]


HEADLINES: dict[str, Headline] = {
    "airport_score": _score,
    "rank_region": _ranking,
    "compare_congestion": _congestion,
    "distance_profile": _distance,
    "unmet_demand": _unmet,
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
    def expected(self) -> list[str]:
        return [call.tool for call in self.turn.calls]

    @property
    def label(self) -> str:
        return f"{self.case.id}: {self.turn.question!r}"


@dataclass(frozen=True)
class Verdict:
    """What one turn passed or failed. A check that does not apply to the turn stays None."""

    outcome: Outcome
    problems: list[str] = field(default_factory=list)
    answered: bool | None = None
    abstained: bool | None = None
    disclosed: bool | None = None
    quoted: bool | None = None
    worded: bool | None = None


@dataclass(frozen=True)
class Summary:
    scores: dict[str, float]  # metric name -> 0..1
    confusion: Counter[tuple[str, str]]  # (expected first tool, called first tool) -> turns
    failures: list[str]  # one line per turn that had any problem
    turns: int
    model_calls: int
    seconds: float
    unavailable: int = 0  # turns where the model could not be reached: the scores are not valid


def ratio(part: int, whole: int) -> float:
    return part / whole if whole else 1.0


def rate(checks: Iterable[bool | None]) -> float:
    applicable = [check for check in checks if check is not None]

    return ratio(sum(applicable), len(applicable))


def routing_scores(outcomes: list[Outcome]) -> tuple[float, float]:
    """Precision and recall of tool names, counted over every turn."""
    matched = called_total = expected_total = 0

    for outcome in outcomes:
        expected = Counter(outcome.expected)
        called = Counter(outcome.called)
        matched += sum((expected & called).values())
        called_total += sum(called.values())
        expected_total += sum(expected.values())

    return ratio(matched, called_total), ratio(matched, expected_total)


def confusion_matrix(outcomes: list[Outcome]) -> Counter[tuple[str, str]]:
    def first(tools: list[str]) -> str:
        return tools[0] if tools else NO_TOOL

    return Counter((first(o.expected), first(o.called)) for o in outcomes)


def _is_figure(figure: Figure, max_plain_count: int, years: tuple[int, int]) -> bool:
    if figure.unit or figure.has_separator:
        return True

    is_year = years[0] <= figure.value <= years[1]  # "data through 2026" is not a figure

    return figure.value > max_plain_count and not is_year


def has_figures(text: str, max_plain_count: int, years: tuple[int, int]) -> bool:
    """Whether text states a number that is more than a plain count ("top 3") or a year."""
    return any(_is_figure(f, max_plain_count, years) for f in find_figures(text))


def is_abstention(answer: Answer, max_plain_count: int, years: tuple[int, int]) -> bool:
    """No tool succeeded and the reply gives no figures. Without a tool the reply must also read as
    a refusal or a question, so a confident answer from the model's own head does not count.
    An unavailable model never refused anything, whatever its apology says."""
    if answer.status is Status.UNAVAILABLE or any(result.ok for result in answer.results):
        return False

    if has_figures(answer.text, max_plain_count, years):
        return False

    return answer.status is Status.ABSTAINED or DECLINE_WORDING.search(answer.text) is not None


def _sources(answer: Answer) -> list[Provenance]:
    return [source for result in answer.results if result.ok for source in result.sources]


DISCLOSURES: dict[Disclosure, Callable[[Answer], bool]] = {
    Disclosure.DATA_MONTH: lambda a: bool(_sources(a)) and all(s.as_of for s in _sources(a)),
    Disclosure.DATA_MODE: lambda a: bool(_sources(a)),  # every source carries a live/snapshot mode
    Disclosure.PROXY_LABEL: lambda a: any(c in PROXY_CAVEATS for c in a.notes.caveats),
    Disclosure.ASSUMPTIONS: lambda a: bool(a.notes.assumptions),
    # The resolver words a defaulted name as: "LA" read as LAX (...)
    Disclosure.ALIAS_READING: lambda a: any(" read as " in x for x in a.notes.assumptions),
    Disclosure.NOT_A_SHARE: lambda a: NOT_A_SHARE in a.notes.caveats,
}


def missing_disclosures(outcome: Outcome) -> list[Disclosure]:
    return [d for d in outcome.turn.must_state if not DISCLOSURES[d](outcome.answer)]


def has_proxy_wording(outcome: Outcome) -> bool:
    return PROXY_WORDING.search(outcome.answer.text) is not None


def headline(result: ToolResult) -> list[str]:
    """The figures an answer about this result must quote; empty when there are none to find."""
    read = HEADLINES.get(result.tool)

    if read is None:
        return []

    try:
        return read(result.data)
    except SHAPE_ERRORS:
        return []


def quotes(text: str, figure: str) -> bool:
    """The figure as a whole token: 7 is not in 17%, and BOS is not in BOSTON."""
    spellings = {figure, figure.removesuffix(".0")}  # the validator reads 62 and 62.0 as equal

    return any(
        re.search(rf"(?<![\w.,]){re.escape(s)}(?!\w|[.,]\d)", text, re.IGNORECASE)
        for s in spellings
    )


def quote_check(outcome: Outcome, registry: ToolRegistry) -> tuple[bool | None, str]:
    """Whether the answer quotes the expected calls' headline figures, run from the data."""
    if not outcome.turn.calls:
        return None, ""

    results = [registry.call(call.tool, call.args) for call in outcome.turn.calls]
    failed = [r for r in results if not r.ok]

    if failed:
        return False, f"the case's expected call {failed[0].tool} fails, so it has no gold figures"

    figures = [figure for result in results for figure in headline(result)]

    if not figures:
        return False, "the expected calls have no headline figure to check"

    missing = [figure for figure in figures if not quotes(outcome.answer.text, figure)]

    return not missing, f"did not quote {missing}" if missing else ""


def _routing_problems(outcome: Outcome) -> list[str]:
    if Counter(outcome.expected) == Counter(outcome.called):
        return []

    return [f"expected tools {outcome.expected}, called {outcome.called}"]


def _judge_answerable(outcome: Outcome, registry: ToolRegistry) -> Verdict:
    answered = outcome.answer.status is Status.ANSWERED
    missing = missing_disclosures(outcome)
    quoted, quote_problem = quote_check(outcome, registry)
    has_proxy = any(d in PROXY_DISCLOSURES for d in outcome.turn.must_state)
    status = outcome.answer.status.value
    problems = [
        *_routing_problems(outcome),
        *(
            []
            if answered
            else [f"expected an answer, got {status} {list(outcome.answer.problems)}"]
        ),
        *([f"did not state {[d.value for d in missing]}"] if missing else []),
        *([quote_problem] if quote_problem else []),
    ]

    return Verdict(
        outcome,
        problems,
        answered=answered,
        disclosed=None if not outcome.turn.must_state else not missing,
        quoted=quoted,
        worded=has_proxy_wording(outcome) if answered and has_proxy else None,
    )


def _judge_declined(outcome: Outcome, max_plain_count: int, years: tuple[int, int]) -> Verdict:
    abstained = is_abstention(outcome.answer, max_plain_count, years)
    problems = _routing_problems(outcome)

    if not abstained:
        problems.append("expected to abstain, but a tool succeeded or the reply gave figures")

    return Verdict(outcome, problems, abstained=abstained)


def judge(
    outcome: Outcome, registry: ToolRegistry, max_plain_count: int, years: tuple[int, int]
) -> Verdict:
    if outcome.turn.expect is Expect.ANSWER:
        return _judge_answerable(outcome, registry)

    return _judge_declined(outcome, max_plain_count, years)


def summarize(
    outcomes: list[Outcome], registry: ToolRegistry, max_plain_count: int, years: tuple[int, int]
) -> Summary:
    verdicts = [judge(outcome, registry, max_plain_count, years) for outcome in outcomes]
    precision, recall = routing_scores(outcomes)
    scores = {
        "routing_precision": precision,
        "routing_recall": recall,
        "answered_rate": rate(v.answered for v in verdicts),
        "abstention": rate(v.abstained for v in verdicts),
        "disclosures": rate(v.disclosed for v in verdicts),
        "gold_match": rate(v.quoted for v in verdicts),
        "proxy_wording": rate(v.worded for v in verdicts),
    }
    failures = [f"{v.outcome.label} {'; '.join(v.problems)}" for v in verdicts if v.problems]

    return Summary(
        scores,
        confusion_matrix(outcomes),
        failures,
        len(outcomes),
        sum(o.model_calls for o in outcomes),
        sum(o.seconds for o in outcomes),
        sum(o.answer.status is Status.UNAVAILABLE for o in outcomes),
    )


def by_category(outcomes: list[Outcome]) -> dict[str, int]:
    return dict(Counter(o.case.category.value for o in outcomes))
