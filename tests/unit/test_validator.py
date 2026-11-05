from datetime import UTC, datetime
from typing import Any

import pytest

from airport_agent.dataset import Dataset
from airport_agent.domain import DataMode, Provenance
from airport_agent.tools import build_registry
from airport_agent.tools.result import ToolResult
from airport_agent.validator import ViolationKind, gather_evidence, validate_answer

COVERAGE = {"BOS", "SFO", "ANC", "JFK", "LAX"}


def _result(**fields: Any) -> ToolResult:
    return ToolResult(tool="t", ok=True, **fields)


def _check(answer: str, *results: ToolResult, question: str = "") -> list[str]:
    evidence = gather_evidence(results, question)

    return [v.text for v in validate_answer(answer, evidence, coverage=COVERAGE)]


DATA = _result(
    data={
        "airport": {"iata": "SFO"},
        "delay_rate": {"value": 0.253, "shown": "25.3%"},
        "avg_distance": {"value": 1860.0, "shown": "1,860 miles"},
        "score": 62.2,
        "rank": 1,
    },
    assumptions=["Load factor: weight 0.30, 50.0% scores 0"],
    caveats=["Data is as of 2026-08."],
)


@pytest.mark.parametrize(
    "answer",
    [
        "SFO has 25.3% of flights late.",
        "The delay rate is 0.253.",
        "Flights average 1,860 miles, or 1860 miles.",
        "The score is 62.2.",
        "Load factor has weight 0.3 and a floor of 50%.",
        "That is a fall of 25.30%.",
    ],
)
def test_numbers_copied_from_a_tool_result_pass(answer: str) -> None:
    assert _check(answer, DATA) == []


@pytest.mark.parametrize(
    ("answer", "bad"),
    [
        ("About 25% of flights are late.", ["25%"]),
        ("Delays are 30.1% worse.", ["30.1%"]),
        ("The score is 63.", ["63"]),
        ("Average distance is 1,900 miles.", ["1,900 miles"]),
        ("It carries 12 million passengers.", ["12"]),
    ],
)
def test_numbers_not_in_any_result_are_flagged(answer: str, bad: list[str]) -> None:
    assert _check(answer, DATA) == bad


def test_the_sign_does_not_matter() -> None:
    result = _result(data={"growth": {"value": -0.031, "shown": "-3.1%"}})

    assert _check("Traffic fell 3.1%.", result) == []


@pytest.mark.parametrize(
    "answer",
    [
        "Data through 2026-07 and July 2026.",
        "In 2026 it was the 3rd busiest.",
        "The top 5 airports; 2 of 4 signals; 0 cancellations.",
    ],
)
def test_dates_years_ordinals_and_small_counts_are_exempt(answer: str) -> None:
    assert _check(answer, DATA) == []


@pytest.mark.parametrize(
    "answer", ["Growth was 5%.", "A gap of 7 pts.", "About 9 miles.", "It is 11."]
)
def test_small_numbers_with_a_unit_or_above_ten_are_still_checked(answer: str) -> None:
    assert _check(answer, DATA) != []


def test_digits_in_a_date_do_not_legitimise_other_numbers() -> None:
    result = _result(caveats=["Data as of 2026-11, fetched 2026-11-04."])

    assert _check("Delays rose 11%.", result) == ["11%"]


def test_provenance_timestamps_are_not_evidence() -> None:
    source = Provenance(
        source="x", mode=DataMode.LIVE, fetched_at=datetime(2026, 11, 4, 22, 59, 49, tzinfo=UTC)
    )
    result = _result(sources=[source])

    assert _check("It rose 59%.", result) == ["59%"]


def test_a_number_the_user_wrote_may_be_repeated() -> None:
    assert _check("Here are the top 15 airports.", DATA, question="show the top 15") == []


def test_booleans_do_not_count_as_numbers() -> None:
    result = _result(data={"above_threshold": True})

    assert _check("It is 1,000 miles.", result) == ["1,000 miles"]


@pytest.mark.parametrize(
    ("answer", "bad"),
    [("SFO and JFK are busy.", ["JFK"]), ("Try LAX or JFK.", ["JFK", "LAX"])],
)
def test_airports_that_no_result_mentions_are_flagged(answer: str, bad: list[str]) -> None:
    assert _check(answer, DATA) == bad


def test_airport_codes_in_options_and_the_question_are_allowed() -> None:
    options = _result(data={"options": ["JFK", "LAX"]})

    assert _check("JFK, LAX or SFO?", options) == ["SFO"]
    assert _check("JFK, LAX or SFO?", options, question="and SFO?") == []
    assert _check("You asked about BOS.", DATA, question="how is bos doing") == []


def test_ordinary_capitalised_words_are_not_airports() -> None:
    assert _check("THE USA AND ITS airports.", DATA) == []


def test_violations_say_what_kind_they_are() -> None:
    evidence = gather_evidence([DATA])
    kinds = {v.kind for v in validate_answer("JFK has 99.9%.", evidence, coverage=COVERAGE)}

    assert kinds == {ViolationKind.NUMBER, ViolationKind.AIRPORT}


def test_an_honest_answer_built_from_a_real_score_passes(dataset: Dataset) -> None:
    result = build_registry(dataset).call("airport_score", {"airport": "BOS"})
    parts = [c["shown"] for c in result.data["components"]]
    answer = (
        f"BOS scores {result.data['score']} (confidence {result.data['confidence']}): "
        + ", ".join(parts)
    )
    evidence = gather_evidence([result], "how is BOS?")

    assert validate_answer(answer, evidence, coverage=dataset.settings.coverage_airports) == []


def test_changing_one_digit_of_a_real_answer_is_caught(dataset: Dataset) -> None:
    result = build_registry(dataset).call("airport_score", {"airport": "BOS"})
    wrong = f"BOS scores {result.data['score'] + 0.1:.1f}."
    evidence = gather_evidence([result])

    found = validate_answer(wrong, evidence, coverage=dataset.settings.coverage_airports)

    assert [v.kind for v in found] == [ViolationKind.NUMBER]


def test_every_covered_airport_can_quote_its_own_score(dataset: Dataset) -> None:
    registry = build_registry(dataset)

    for code in dataset.settings.coverage_airports:
        result = registry.call("airport_score", {"airport": code})
        quotes = [f"{code} scores {result.data['score']}"] if result.ok else []
        evidence = gather_evidence([result], f"score {code}")

        assert (
            validate_answer(" ".join(quotes), evidence, coverage=dataset.settings.coverage_airports)
            == []
        )


def test_the_score_scale_can_be_quoted_because_the_tool_states_it(dataset: Dataset) -> None:
    result = build_registry(dataset).call("airport_score", {"airport": "BOS"})
    answer = f"BOS scores {result.data['score']} out of 100."
    evidence = gather_evidence([result])

    assert validate_answer(answer, evidence, coverage=dataset.settings.coverage_airports) == []
