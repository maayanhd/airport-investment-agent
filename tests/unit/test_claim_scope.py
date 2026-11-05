"""Claims are checked against covered airports only, and against the ranking they came from."""

from airport_agent.evidence import gather_evidence
from airport_agent.tools.result import ToolResult
from airport_agent.validator import Rules, validate_answer

RULES = Rules({"BOS", "PVD", "MHT"}, max_plain_count=10, years=(1990, 2099))


def _ranking(*codes: str) -> ToolResult:
    rows = [{"rank": n, "airport": {"iata": c}} for n, c in enumerate(codes, start=1)]

    return ToolResult(tool="rank_region", ok=True, data={"ranking": rows})


def _problems(answer: str, *results: ToolResult) -> list[str]:
    return [v.text for v in validate_answer(answer, gather_evidence(results, ""), RULES)]


def test_a_capitalised_word_before_the_airports_does_not_hide_a_false_comparison() -> None:
    ranking = _ranking("BOS", "PVD")

    assert _problems("PVD is a stronger candidate than BOS.", ranking) != []
    assert _problems("FAA data shows PVD is a stronger candidate than BOS.", ranking) != []
    assert _problems("FAA data shows BOS is a stronger candidate than PVD.", ranking) == []


def test_a_capitalised_word_does_not_hide_a_false_outranks_either() -> None:
    assert _problems("According to the USA, PVD outranks BOS.", _ranking("BOS", "PVD")) != []


def test_an_airport_named_twice_with_two_ranks_is_still_checked() -> None:
    assert _problems("BOS ranks 3rd and BOS ranks 2nd.", _ranking("BOS", "PVD")) != []


def test_a_rank_that_one_of_the_rankings_gave_is_accepted() -> None:
    new_england = _ranking("BOS", "PVD", "MHT")
    northeast = _ranking("PVD", "BOS", "MHT")

    assert _problems("BOS ranks 1st in New England.", new_england, northeast) == []
    assert _problems("BOS ranks 2nd in the Northeast.", new_england, northeast) == []
    assert _problems("BOS ranks 3rd.", new_england, northeast) != []


def test_a_comparison_is_judged_inside_a_ranking_that_has_both_airports() -> None:
    ranking = _ranking("PVD", "BOS")
    elsewhere = _ranking("MHT", "PVD")

    assert _problems("BOS outranks PVD.", ranking, elsewhere) != []
    assert _problems("BOS outranks MHT.", ranking, elsewhere) == []  # never ranked together
