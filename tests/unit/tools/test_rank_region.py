import dataclasses

import pytest

from airport_agent.data.history import History
from airport_agent.dataset import Dataset
from airport_agent.domain import MonthlyTraffic
from airport_agent.tools import build_registry
from airport_agent.tools.result import ErrorKind, ToolResult


def _rank(dataset: Dataset, region: str, **args: object) -> ToolResult:
    return build_registry(dataset).call("rank_region", {"region": region, **args})


def test_new_england_is_ranked_best_first_inside_its_states(dataset: Dataset) -> None:
    result = _rank(dataset, "New England", top_n=20)
    rows = result.data["ranking"]
    states = {row["airport"]["state"] for row in rows}

    assert result.ok
    assert states <= {"CT", "MA", "ME", "NH", "RI", "VT"}
    assert [r["rank"] for r in rows] == list(range(1, len(rows) + 1))
    order = [(not r["complete"], -r["score"]) for r in rows]
    assert order == sorted(order)  # complete scores first, then best first
    assert len(rows) + len(result.data["not_scored"]) == result.data["covered_airports"]


def test_top_n_limits_the_list_and_the_name_is_read_loosely(dataset: Dataset) -> None:
    result = _rank(dataset, "  new   ENGLAND ", top_n=3)

    assert len(result.data["ranking"]) == 3
    assert "CT, MA, ME, NH, RI, VT" in result.assumptions[0]


def test_a_state_code_works_as_a_region(dataset: Dataset) -> None:
    result = _rank(dataset, "ak")

    assert {r["airport"]["state"] for r in result.data["ranking"]} == {"AK"}


def test_an_unknown_region_lists_the_ones_it_knows(dataset: Dataset) -> None:
    result = _rank(dataset, "Atlantis")

    assert result.error is not None
    assert (result.error.kind, result.error.options) == (ErrorKind.BAD_ARGUMENTS, ["new england"])


def test_a_state_without_covered_airports_is_not_covered(dataset: Dataset) -> None:
    result = _rank(dataset, "ZZ")

    assert result.error is not None
    assert result.error.kind is ErrorKind.NOT_COVERED


@pytest.mark.parametrize("top_n", [0, 21])
def test_top_n_outside_its_range_is_a_bad_argument(dataset: Dataset, top_n: int) -> None:
    result = _rank(dataset, "New England", top_n=top_n)

    assert result.error is not None
    assert result.error.kind is ErrorKind.BAD_ARGUMENTS


def test_a_region_with_no_scores_fails_with_insufficient_data(dataset: Dataset) -> None:
    nothing = History[MonthlyTraffic]({}, dataset.traffic.provenance, [])
    empty = dataclasses.replace(dataset, traffic=nothing)
    result = _rank(empty, "New England")

    assert result.error is not None
    assert result.error.kind is ErrorKind.INSUFFICIENT_DATA


def test_a_strong_partial_score_below_the_cut_is_named(dataset: Dataset) -> None:
    result = _rank(dataset, "New England", top_n=5)

    assert any("HVN" in line and "not shown" in line for line in result.caveats)
    assert any("ranked before airports with partial data" in a for a in result.assumptions)


def test_nothing_is_reported_missing_when_every_partial_airport_is_shown(dataset: Dataset) -> None:
    result = _rank(dataset, "New England", top_n=20)

    assert not any("not shown" in line for line in result.caveats)


def test_a_state_code_that_is_also_a_nickname_points_to_the_airport_names(dataset: Dataset) -> None:
    result = _rank(dataset, "LA")

    assert "name its airports instead" in result.assumptions[0]
