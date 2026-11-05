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
    assert rows["SFO"]["delay_rate"]["shown"] == "25.3%"


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
