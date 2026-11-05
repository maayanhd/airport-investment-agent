import dataclasses

from airport_agent.data.history import History
from airport_agent.dataset import Dataset
from airport_agent.domain import MonthlyTraffic
from airport_agent.scoring import COMPONENTS
from airport_agent.tools import build_registry
from airport_agent.tools.result import ErrorKind, ToolResult


def _unmet(dataset: Dataset, airport: str) -> ToolResult:
    return build_registry(dataset).call("unmet_demand", {"airport": airport})


def test_each_signal_is_marked_strong_or_not_from_the_fixed_range(dataset: Dataset) -> None:
    result = _unmet(dataset, "SFO")
    signals = result.data["signals"]

    assert result.ok
    assert [s["name"] for s in signals] == list(COMPONENTS)
    assert result.data["strong_signals"] == sum(s["strong"] for s in signals)
    assert result.data["total_signals"] == len(signals)
    assert {s["name"]: s["strong"] for s in signals}["load_factor"]


def test_it_says_unmet_demand_is_not_observed(dataset: Dataset) -> None:
    caveats = " ".join(_unmet(dataset, "SFO").caveats)

    assert "cannot be observed" in caveats


def test_an_airport_with_no_traffic_data_fails_with_the_reason(dataset: Dataset) -> None:
    nothing = History[MonthlyTraffic]({}, dataset.traffic.provenance, [])
    result = _unmet(dataclasses.replace(dataset, traffic=nothing), "SFO")

    assert result.error is not None
    assert result.error.kind is ErrorKind.INSUFFICIENT_DATA


def test_a_partial_signal_set_is_flagged_and_lists_what_is_missing(dataset: Dataset) -> None:
    result = _unmet(dataset, "HVN")  # no delay data for HVN

    assert result.ok
    assert result.data["missing"] == ["delay_pressure"]
    assert result.data["total_signals"] == 3
    assert any("built from" in line for line in result.caveats)


def test_an_unclear_airport_is_returned_for_the_agent_to_ask(dataset: Dataset) -> None:
    result = _unmet(dataset, "Portland")

    assert result.error is not None
    assert result.error.kind is ErrorKind.AMBIGUOUS_AIRPORT
