import dataclasses

from airport_agent.data.history import History
from airport_agent.dataset import Dataset
from airport_agent.domain import MonthlyTraffic
from airport_agent.tools import build_registry
from airport_agent.tools.result import ErrorKind, ToolResult


def _profile(dataset: Dataset, airport: str) -> ToolResult:
    return build_registry(dataset).call("distance_profile", {"airport": airport})


def test_a_long_distance_airport_is_above_the_threshold(dataset: Dataset) -> None:
    data = _profile(dataset, "ANC").data

    assert data["above_threshold"] is True
    assert data["avg_distance"]["value"] >= data["long_haul_threshold"]["value"] == 1500


def test_a_short_distance_airport_is_below_the_threshold(dataset: Dataset) -> None:
    data = _profile(dataset, "BDL").data

    assert data["above_threshold"] is False


def test_it_never_claims_a_long_haul_share(dataset: Dataset) -> None:
    result = _profile(dataset, "ANC")

    assert "not the share of long-haul flights" in result.caveats[0]
    assert "share" not in result.data


def test_an_unknown_airport_is_an_error_not_a_guess(dataset: Dataset) -> None:
    result = _profile(dataset, "Atlantis")

    assert result.error is not None
    assert result.error.kind is ErrorKind.UNKNOWN_AIRPORT


def test_an_airport_without_enough_traffic_months_fails_with_insufficient_data(
    dataset: Dataset,
) -> None:
    nothing = History[MonthlyTraffic]({}, dataset.traffic.provenance, [])
    result = _profile(dataclasses.replace(dataset, traffic=nothing), "ANC")

    assert result.error is not None
    assert result.error.kind is ErrorKind.INSUFFICIENT_DATA


def test_the_flag_agrees_with_the_rounded_average_shown(dataset: Dataset) -> None:
    data = _profile(dataset, "ANC").data

    assert data["above_threshold"] == (data["avg_distance"]["value"] >= 1500)
