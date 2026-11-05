from datetime import UTC, datetime
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from airport_agent.dataset import Dataset
from airport_agent.domain import DataMode, Provenance
from airport_agent.evidence import gather_evidence
from airport_agent.tools import build_registry
from airport_agent.tools.result import ToolResult
from airport_agent.validator import Rules, ViolationKind, validate_answer

COVERAGE = {"BOS", "SFO", "ANC", "JFK", "LAX", "SEA", "SNA"}
RULES = Rules(COVERAGE, max_plain_count=10, years=(1990, 2099))


def _result(**fields: Any) -> ToolResult:
    return ToolResult(tool="t", ok=True, **fields)


def _check(answer: str, *results: ToolResult, question: str = "") -> list[str]:
    evidence = gather_evidence(results, question)

    return [v.text for v in validate_answer(answer, evidence, RULES)]


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
        "That is 25.30%.",
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
        ("It carries 12 million passengers.", ["12 million"]),
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
    assert _check("You asked about BOS.", DATA, question="how is BOS doing") == []


def test_ordinary_capitalised_words_are_not_airports() -> None:
    assert _check("THE USA AND ITS airports.", DATA) == []


def test_violations_say_what_kind_they_are() -> None:
    evidence = gather_evidence([DATA])
    kinds = {v.kind for v in validate_answer("JFK has 99.9%.", evidence, RULES)}

    assert kinds == {ViolationKind.NUMBER, ViolationKind.AIRPORT}


def test_an_honest_answer_built_from_a_real_score_passes(dataset: Dataset) -> None:
    result = build_registry(dataset).call("airport_score", {"airport": "BOS"})
    parts = [c["shown"] for c in result.data["components"]]
    answer = (
        f"BOS scores {result.data['score']} (confidence {result.data['confidence']}): "
        + ", ".join(parts)
    )
    evidence = gather_evidence([result], "how is BOS?")

    assert validate_answer(answer, evidence, Rules.from_settings(dataset.settings)) == []


def test_changing_one_digit_of_a_real_answer_is_caught(dataset: Dataset) -> None:
    result = build_registry(dataset).call("airport_score", {"airport": "BOS"})
    wrong = f"BOS scores {result.data['score'] + 0.1:.1f}."
    evidence = gather_evidence([result])

    found = validate_answer(wrong, evidence, Rules.from_settings(dataset.settings))

    assert [v.kind for v in found] == [ViolationKind.NUMBER]


def test_every_covered_airport_can_quote_its_own_score(dataset: Dataset) -> None:
    registry = build_registry(dataset)
    coverage = dataset.settings.coverage_airports
    checked = 0

    for code in coverage:
        result = registry.call("airport_score", {"airport": code})

        if result.ok:
            evidence = gather_evidence([result], f"score {code}")
            answer = f"{code} scores {result.data['score']}"
            checked += 1

            assert validate_answer(answer, evidence, Rules.from_settings(dataset.settings)) == []

    assert checked > 0.9 * len(coverage)


@pytest.mark.parametrize(
    "answer",
    [
        "About a quarter of flights are late.",
        "Roughly twelve million passengers.",
        "Half of the airports are strong.",
        "Delays doubled.",
        "Hundreds of flights.",
        "\u00bd of flights.",
        "A third of airports.",
    ],
)
def test_numbers_written_as_words_are_flagged(answer: str) -> None:
    assert _check(answer, DATA) != []


def test_small_number_words_and_ordinals_are_fine() -> None:
    assert _check("The third busiest of the top five, one of two.", DATA) == []


def test_a_number_word_the_question_used_may_be_repeated() -> None:
    assert _check("Yes, about half.", DATA, question="is it about half?") == []


def test_a_year_is_exempt_only_in_a_year_position() -> None:
    assert _check("Growth since 2024 was strong.", DATA) == []
    assert _check("It handles 2024 flights.", DATA) == ["2024"]


def test_a_negative_value_can_be_quoted_with_or_without_its_sign() -> None:
    result = _result(data={"gap": -0.031})

    assert _check("The gap is -0.031.", result) == []
    assert _check("The gap is 0.031.", result) == []


def test_a_fraction_can_be_quoted_as_a_percent() -> None:
    result = _result(data={"confidence": 0.85})

    assert _check("Confidence is 85%.", result) == []
    assert _check("Confidence is 86%.", result) == ["86%"]


def test_airport_codes_used_as_keys_count_as_mentioned() -> None:
    result = _result(data={"BOS": {"x": 1}, "SFO": {"x": 2}})

    assert _check("BOS grew faster than SFO.", result) == []


def test_only_codes_written_in_capitals_in_the_question_are_allowed() -> None:
    assert _check("SEA is strong.", DATA, question="airports near the sea") == ["SEA"]
    assert _check("SEA is strong.", DATA, question="how is SEA?") == []


@given(st.text())
def test_any_answer_gives_a_list_and_never_raises(answer: str) -> None:
    evidence = gather_evidence([DATA], "question")

    assert isinstance(validate_answer(answer, evidence, RULES), list)


GROWTH = _result(data={"airport": {"iata": "BOS"}, "growth": {"value": -0.023, "shown": "-2.3%"}})
RISING = _result(data={"airport": {"iata": "SEA"}, "growth": {"value": 0.041, "shown": "4.1%"}})


@pytest.mark.parametrize(
    "answer",
    ["BOS traffic fell 2.3%.", "BOS traffic fell by 2.3%.", "Growth was -2.3%.", "It is 2.3%."],
)
def test_a_fall_may_be_quoted_in_the_ways_people_write_it(answer: str) -> None:
    assert _check(answer, GROWTH) == []


@pytest.mark.parametrize(
    ("answer", "bad"),
    [
        ("BOS traffic grew 2.3%.", ["2.3% (the tools give it as a fall)"]),
        ("BOS traffic is up 2.3%.", ["2.3% (the tools give it as a fall)"]),
        ("BOS growth was +2.3%.", ["+2.3% (the tools give it as negative)"]),
    ],
)
def test_a_flipped_direction_is_flagged(answer: str, bad: list[str]) -> None:
    assert _check(answer, GROWTH) == bad


def test_a_positive_value_cannot_be_called_a_fall_or_given_a_minus() -> None:
    assert _check("SEA traffic dropped 4.1%.", RISING) == ["4.1% (the tools give it as a rise)"]
    assert _check("SEA growth was -4.1%.", RISING) == ["-4.1% (the tools give it without a minus)"]
    assert _check("SEA traffic rose 4.1%.", RISING) == []


def test_a_level_after_a_direction_word_is_not_read_as_a_change() -> None:
    assert _check("Delays fell to 2.3% of flights.", GROWTH) == []


def test_a_hyphen_in_a_range_is_not_a_minus_sign() -> None:
    result = _result(data={"a": 5, "b": 10.5})

    assert _check("Between 5-10.5 hours.", result) == []


def test_an_unwritten_sign_is_not_enough_to_flag_a_number() -> None:
    result = _result(data={"airport": {"iata": "BOS"}, "gap": -0.031}, caveats=["A 3.1 gap"])

    assert _check("The gap is 3.1 points.", result) == []


BOTH = _result(
    data={
        "ranking": [
            {"rank": 1, "airport": {"iata": "BOS"}, "score": 62.2},
            {"rank": 2, "airport": {"iata": "SFO"}, "score": 55.1},
        ],
        "covered_airports": 12,
    }
)


def test_a_number_from_another_airport_is_flagged() -> None:
    assert _check("BOS scores 62.2.", BOTH) == []
    assert _check("SFO scores 62.2.", BOTH) == ["62.2 (not a figure for SFO)"]


def test_a_sentence_naming_two_airports_may_use_either_ones_number() -> None:
    assert _check("BOS scores 62.2 and SFO scores 55.1.", BOTH) == []


def test_a_shared_number_fits_every_airport() -> None:
    assert _check("SFO is one of 12 covered airports, 12.0 in all.", BOTH) == []


def test_each_line_of_a_list_is_checked_on_its_own() -> None:
    assert _check("- BOS: 62.2\n- SFO: 55.1", BOTH) == []
    assert _check("- BOS: 55.1\n- SFO: 55.1", BOTH) == ["55.1 (not a figure for BOS)"]


@pytest.mark.parametrize(
    "answer",
    [
        "BOS ranks 1st.",
        "SFO is ranked #2.",
        "BOS is the strongest candidate.",
        "BOS ranks ahead of SFO.",
        "BOS is a stronger candidate than SFO.",
        "SFO has a lower score than BOS.",
    ],
)
def test_rank_claims_that_match_the_tool_pass(answer: str) -> None:
    assert _check(answer, BOTH) == []


@pytest.mark.parametrize(
    "answer",
    [
        "SFO ranks 1st.",
        "BOS is ranked #2.",
        "SFO is the strongest candidate.",
        "SFO outranks BOS.",
        "SFO is a stronger candidate than BOS.",
        "BOS has a lower score than SFO.",
    ],
)
def test_rank_claims_that_contradict_the_tool_are_flagged(answer: str) -> None:
    assert _check(answer, BOTH) != []


COMPARE = _result(
    data={
        "airports": [
            {"airport": {"iata": "LAX"}, "delay_rate": {"value": 0.25, "shown": "25.0%"}},
            {"airport": {"iata": "SNA"}, "delay_rate": {"value": 0.18, "shown": "18.0%"}},
        ],
        "more_delayed": "LAX",
    }
)


@pytest.mark.parametrize(
    "answer",
    [
        "LAX is more congested than SNA.",
        "SNA has fewer delays than LAX.",
        "SNA is less delayed than LAX.",
        "SNA has better on-time flights than LAX, and a lower delay rate than LAX.",
    ],
)
def test_a_congestion_comparison_that_matches_the_tool_passes(answer: str) -> None:
    assert _check(answer, COMPARE) == []


@pytest.mark.parametrize(
    "answer",
    [
        "SNA is more congested than LAX.",
        "LAX has fewer delays than SNA.",
        "LAX is less delayed than SNA.",
    ],
)
def test_a_congestion_comparison_the_wrong_way_round_is_flagged(answer: str) -> None:
    found = validate_answer(answer, gather_evidence([COMPARE]), RULES)

    assert [v.kind for v in found] == [ViolationKind.CLAIM]


def test_a_comparison_with_no_tool_decision_is_not_checked() -> None:
    undecided = _result(data={"airports": []})

    assert _check("SNA is more congested than LAX.", undecided, question="SNA or LAX?") == []


CITIES = {"boston": frozenset({"BOS"}), "new york": frozenset({"JFK", "LGA"})}


def test_a_covered_city_named_in_words_needs_a_tool_result_for_it() -> None:
    rules = Rules(COVERAGE, max_plain_count=10, years=(1990, 2099), city_codes=CITIES)
    evidence = gather_evidence([RISING], "how is Seattle?")

    found = validate_answer("Boston is bigger, as is New York.", evidence, rules)

    assert [v.text for v in found] == ["boston", "new york"]


def test_a_city_in_the_question_or_behind_a_returned_airport_is_allowed() -> None:
    rules = Rules(COVERAGE, max_plain_count=10, years=(1990, 2099), city_codes=CITIES)

    asked = gather_evidence([RISING], "what about Boston?")
    returned = gather_evidence([GROWTH], "")

    assert validate_answer("Boston looks fine.", asked, rules) == []
    assert validate_answer("Boston looks fine.", returned, rules) == []


def test_real_congestion_and_ranking_results_feed_the_claim_checks(dataset: Dataset) -> None:
    registry = build_registry(dataset)
    compare = registry.call("compare_congestion", {"airport_a": "SFO", "airport_b": "ANC"})
    ranking = registry.call("rank_region", {"region": "New England", "top_n": 2})
    evidence = gather_evidence([compare, ranking], "SFO vs ANC, and New England")
    worse = compare.data["more_delayed"]  # a clear gap: LAX vs SNA is now called a tie
    better = "ANC" if worse == "SFO" else "SFO"
    first, second = (row["airport"]["iata"] for row in ranking.data["ranking"])
    rules = Rules.from_settings(dataset.settings)

    right = f"{worse} is more delayed than {better}. {first} is a stronger candidate than {second}."
    wrong = f"{better} is more delayed than {worse}. {second} is a stronger candidate than {first}."

    assert validate_answer(right, evidence, rules) == []
    assert [v.kind for v in validate_answer(wrong, evidence, rules)] == [ViolationKind.CLAIM] * 2


@pytest.mark.parametrize(
    "answer",
    [
        "Declined 1500 departures last year.",
        "Marched 3000 seats into the schedule.",
        "May be 2400 passengers, in May 1999 terms.",
    ],
)
def test_a_word_that_starts_like_a_month_does_not_hide_a_number(answer: str) -> None:
    assert _check(answer, DATA) != []


def test_a_real_month_and_year_is_still_a_date() -> None:
    assert _check("Data as of November 2026, Sep 2025 and Dec. 2025.", DATA) == []


def test_worse_means_behind_for_a_candidate_and_ahead_for_delays() -> None:
    assert _check("SFO is a worse candidate than BOS.", BOTH) == []
    assert _check("BOS is a worse candidate than SFO.", BOTH) != []
    assert _check("LAX has worse delays than SNA.", COMPARE) == []
    assert _check("SNA has worse delays than LAX.", COMPARE) != []


def test_an_ordinal_that_is_not_a_rank_is_not_a_rank_claim() -> None:
    assert _check("SFO growth slowed in the 3rd period, and in the 12th month.", BOTH) == []
    assert _check("SFO is in 2nd place.", BOTH) == []
    assert _check("SFO is in 1st place.", BOTH) != []


def test_up_and_down_count_as_a_direction_only_as_a_state() -> None:
    assert _check("SEA is up 4.1%.", RISING) == []
    assert _check("BOS is up 2.3%.", GROWTH) != []
    assert _check("Settings are set up 2.3%.", GROWTH) == []
    assert _check("Delays are down to 2.3% of flights.", GROWTH) == []


def test_a_city_inside_a_returned_airports_name_is_allowed() -> None:
    cities = {"dallas": frozenset({"DAL"})}
    rules = Rules(COVERAGE, max_plain_count=10, years=(1990, 2099), city_codes=cities)
    named = _result(data={"airport": {"iata": "DFW", "name": "Dallas/Fort Worth International"}})

    assert validate_answer("Dallas Fort Worth looks fine.", gather_evidence([named]), rules) == []


def test_a_rank_after_an_abbreviated_no_is_still_checked() -> None:
    assert _check("BOS is ranked No. 1.", BOTH) == []
    assert _check("SFO is ranked No. 1.", BOTH) == ["SFO ranked 1"]


def test_a_figure_from_another_airport_cannot_be_given_to_one_the_tools_never_returned() -> None:
    only_sfo = _result(data={"airport": {"iata": "SFO"}, "score": 55.1})

    assert _check("BOS scores 55.1.", only_sfo, question="and BOS?") == [
        "55.1 (not a figure for BOS)"
    ]
    assert _check("BOS is not covered by that result.", only_sfo, question="and BOS?") == []


@pytest.mark.parametrize(
    "answer",
    ["SFO is the second strongest candidate.", "SFO is not the strongest.", "SFO is 2nd best."],
)
def test_a_qualified_superlative_is_not_a_claim_to_be_first(answer: str) -> None:
    assert _check(answer, BOTH) == []


def test_a_higher_score_is_checked_against_the_score_not_the_rank() -> None:
    partial = _result(
        data={
            "ranking": [
                {"rank": 1, "airport": {"iata": "BOS"}, "score": 50.0},
                {"rank": 2, "airport": {"iata": "SEA"}, "score": 70.0},
            ]
        }
    )

    assert _check("SEA has a higher score than BOS.", partial) == []
    assert _check("BOS has a higher score than SEA.", partial) == [
        "BOS has a higher score than SEA"
    ]


def test_a_figure_between_two_airports_does_not_hide_the_comparison() -> None:
    assert _check("SNA, at 18.0%, is less delayed than LAX.", COMPARE) == []
    assert _check("SNA, at 18.0%, is more delayed than LAX.", COMPARE) == [
        "SNA, at 18.0%, is more delayed than LAX"
    ]


TIED = _result(
    data={
        "airports": [
            {"airport": {"iata": "LAX"}, "delay_rate": {"value": 0.20, "shown": "20.0%"}},
            {"airport": {"iata": "SNA"}, "delay_rate": {"value": 0.201, "shown": "20.1%"}},
        ],
        "more_delayed": None,
        "no_meaningful_difference": True,
    }
)


@pytest.mark.parametrize(
    "answer",
    ["LAX is more delayed than SNA.", "SNA is less delayed than LAX."],
)
def test_a_delay_ranking_of_two_airports_the_tool_called_equal_is_flagged(answer: str) -> None:
    assert _check(answer, TIED) != []


def test_saying_two_tied_airports_are_alike_is_allowed() -> None:
    assert _check("LAX and SNA have similar delay rates, 20.0% and 20.1%.", TIED) == []
