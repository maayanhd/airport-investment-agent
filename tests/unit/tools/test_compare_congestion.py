import dataclasses

from airport_agent.data.history import History
from airport_agent.dataset import Dataset
from airport_agent.domain import MonthlyDelays
from airport_agent.tools import build_registry
from airport_agent.tools.result import ErrorKind, ToolResult


def _compare(dataset: Dataset, a: str, b: str) -> ToolResult:
    return build_registry(dataset).call("compare_congestion", {"airport_a": a, "airport_b": b})


def test_the_airport_with_the_higher_delay_rate_is_named(dataset: Dataset) -> None:
    result = _compare(dataset, "SFO", "ANC")
    rows = {r["airport"]["iata"]: r for r in result.data["airports"]}

    assert result.ok
    assert result.data["more_delayed"] == "SFO"
    assert rows["SFO"]["delay_rate"]["value"] > rows["ANC"]["delay_rate"]["value"]
    assert rows["SFO"]["delay_rate"]["shown"] == f"{rows['SFO']['delay_rate']['value']:.1%}"


def test_the_answer_does_not_depend_on_the_order(dataset: Dataset) -> None:
    forward = _compare(dataset, "SFO", "ANC")
    backward = _compare(dataset, "ANC", "SFO")

    assert forward.data["more_delayed"] == backward.data["more_delayed"] == "SFO"
    assert forward.data["delay_rate_gap"] == backward.data["delay_rate_gap"]


def test_the_proxy_and_its_limits_are_stated(dataset: Dataset) -> None:
    caveats = " ".join(_compare(dataset, "SFO", "ANC").caveats)

    assert "congestion proxy" in caveats
    assert "does not prove it" in caveats


def test_naming_the_same_airport_twice_is_a_bad_argument(dataset: Dataset) -> None:
    result = _compare(dataset, "SFO", "San Francisco")

    assert result.error is not None
    assert result.error.kind is ErrorKind.BAD_ARGUMENTS


def test_an_unclear_second_airport_is_returned_for_the_agent_to_ask(dataset: Dataset) -> None:
    result = _compare(dataset, "SFO", "Portland")

    assert result.error is not None
    assert result.error.kind is ErrorKind.AMBIGUOUS_AIRPORT


def test_an_airport_without_delay_data_is_named_in_the_failure(dataset: Dataset) -> None:
    nothing = History[MonthlyDelays]({}, dataset.delays.provenance if dataset.delays else None, [])  # type: ignore[arg-type]
    result = _compare(dataclasses.replace(dataset, delays=nothing), "SFO", "ANC")

    assert result.error is not None
    assert result.error.kind is ErrorKind.INSUFFICIENT_DATA
    assert "SFO" in result.error.message
    assert "ANC" in result.error.message


def test_the_gap_is_the_difference_of_the_numbers_shown(dataset: Dataset) -> None:
    result = _compare(dataset, "SFO", "ANC")
    first, second = (row["delay_rate"]["value"] for row in result.data["airports"])

    assert result.data["delay_rate_gap"]["value"] == round(abs(first - second), 4)


def test_different_delay_windows_are_called_out(dataset: Dataset) -> None:
    assert dataset.delays is not None
    series = {c: dataset.delays.series(c) for c in dataset.settings.coverage_airports}
    series["ANC"] = series["ANC"][-9:]
    shorter = History(series, dataset.delays.provenance, series, min_months=9)
    result = _compare(dataclasses.replace(dataset, delays=shorter), "SFO", "ANC")
    row = {r["airport"]["iata"]: r["delay_months"] for r in result.data["airports"]}

    assert row == {"SFO": 12, "ANC": 9}
    assert any("different numbers of delay months (SFO 12, ANC 9)" in c for c in result.caveats)


def test_equal_windows_need_no_caveat(dataset: Dataset) -> None:
    caveats = " ".join(_compare(dataset, "SFO", "ANC").caveats)

    assert "different numbers of delay months" not in caveats


def _with_anc_delays(dataset: Dataset, rows: list[MonthlyDelays]) -> Dataset:
    assert dataset.delays is not None
    series = {c: dataset.delays.series(c) for c in dataset.settings.coverage_airports}
    series["ANC"] = rows
    changed = History(series, dataset.delays.provenance, series, min_months=9)

    return dataclasses.replace(dataset, delays=changed)


def test_the_months_used_are_the_newest_consecutive_run_not_all_rows(dataset: Dataset) -> None:
    assert dataset.delays is not None
    rows = dataset.delays.series("ANC")
    gapped = _with_anc_delays(dataset, [*rows[:3], *rows[-10:]])  # months 4-? missing: a gap
    result = _compare(gapped, "SFO", "ANC")
    months = {r["airport"]["iata"]: r["delay_months"] for r in result.data["airports"]}

    assert months["ANC"] == 10


def test_delay_data_ending_in_different_months_is_called_out(dataset: Dataset) -> None:
    assert dataset.delays is not None
    older = _with_anc_delays(dataset, dataset.delays.series("ANC")[:-1])
    result = _compare(older, "SFO", "ANC")

    assert any("ends in different months" in line for line in result.caveats)
