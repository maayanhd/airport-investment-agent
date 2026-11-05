import dataclasses
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from airport_agent.config import Settings
from airport_agent.data.http import HttpFetcher
from airport_agent.data.snapshots import SnapshotStore
from airport_agent.dataset import Dataset, load_dataset
from airport_agent.display import FORMATS, LABELS
from airport_agent.domain import DataMode
from airport_agent.scoring import COMPONENTS
from airport_agent.tools import build_registry
from airport_agent.tools.common import _why_missing
from airport_agent.tools.result import ErrorKind, ToolResult


def _call(dataset: Dataset, airport: str, **settings: Any) -> ToolResult:
    if settings:
        changed = dataset.settings.model_copy(update=settings)
        dataset = dataclasses.replace(dataset, settings=changed)

    return build_registry(dataset).call("airport_score", {"airport": airport})


def test_a_known_airport_returns_every_component_and_adds_up(dataset: Dataset) -> None:
    result = _call(dataset, "BOS")
    data = result.data

    assert result.ok
    assert data["airport"] == {
        "iata": "BOS",
        "name": "Boston Logan International Airport",
        "state": "MA",
    }
    assert [c["name"] for c in data["components"]] == list(COMPONENTS)
    assert sum(c["points"] for c in data["components"]) == pytest.approx(data["score"], abs=0.2)
    assert data["confidence"] == 1.0
    assert data["traffic_through"] == "2026-07"
    assert data["delays_through"] == "2026-08"


def test_numbers_are_rounded_so_they_can_be_quoted_exactly(dataset: Dataset) -> None:
    data = _call(dataset, "BOS").data

    assert data["score"] == round(data["score"], 1)

    for component in data["components"]:
        assert component["points"] == round(component["points"], 1)
        assert component["scaled"] == round(component["scaled"], 2)
        assert component["shown"]  # the text the answer should quote


def test_an_assumed_reading_is_stated_with_the_alternatives(dataset: Dataset) -> None:
    result = _call(dataset, "LA")

    assert result.data["airport"]["iata"] == "LAX"
    assert '"LA" read as LAX' in result.assumptions[0]
    assert "BUR, LGB, ONT, SNA" in result.assumptions[0]


def test_the_scoring_rules_and_the_proxy_are_always_stated(dataset: Dataset) -> None:
    result = _call(dataset, "BOS")

    assert any("weight 0.30" in line for line in result.assumptions)
    assert any("congestion proxy" in line for line in result.caveats)


def test_every_source_is_listed_and_a_snapshot_is_flagged(dataset: Dataset) -> None:
    result = _call(dataset, "BOS")

    assert {s.source.split(" ")[0] for s in result.sources} == {"OurAirports", "BTS"}
    assert any(s.mode is DataMode.SNAPSHOT for s in result.sources)
    assert any("snapshot" in line for line in result.caveats)


def test_an_ambiguous_name_comes_back_as_a_question(dataset: Dataset) -> None:
    result = _call(dataset, "Portland")

    assert result.ok is False
    assert result.error is not None
    assert result.error.kind is ErrorKind.AMBIGUOUS_AIRPORT
    assert result.error.options == ["PDX", "PWM"]
    assert result.data == {}


@pytest.mark.parametrize(
    ("airport", "kind"),
    [("XYZ", ErrorKind.NOT_COVERED), ("Atlantis", ErrorKind.UNKNOWN_AIRPORT)],
)
def test_airports_we_do_not_cover_are_refused_with_a_reason(
    dataset: Dataset, airport: str, kind: ErrorKind
) -> None:
    result = _call(dataset, airport)

    assert result.error is not None
    assert result.error.kind is kind


def test_too_little_data_is_a_failure_that_still_shows_what_exists(dataset: Dataset) -> None:
    result = _call(dataset, "ACK", min_confidence=0.9)  # ACK has no delay data: confidence 0.8

    assert result.ok is False
    assert result.error is not None
    assert result.error.kind is ErrorKind.INSUFFICIENT_DATA
    assert result.data["score"] is None
    assert result.data["missing"] == ["delay_pressure"]


def test_the_result_is_plain_json(dataset: Dataset) -> None:
    text = _call(dataset, "SFO").model_dump_json()

    assert '"tool":"airport_score"' in text


def test_every_component_has_a_label_and_a_format() -> None:
    assert LABELS.keys() == FORMATS.keys() == COMPONENTS.keys()


def test_caveats_name_only_the_source_that_fell_back_to_a_snapshot(dataset: Dataset) -> None:
    caveats = _call(dataset, "BOS").caveats

    # The test fixture cannot reach BTS, so traffic fell back; catalog and delays are snapshots
    # by design and must not add a line.
    assert len(caveats) == 3  # the proxy note, the size note, and the one fallen-back source
    assert "does not measure airport size" in caveats[1]
    assert "T-100" in caveats[2]
    assert "snapshot used" in caveats[2]


def test_a_partial_score_says_what_is_missing_and_why(dataset: Dataset) -> None:
    result = _call(dataset, "ACK")  # seasonal: too few consecutive months of delay data

    assert result.ok
    assert result.data["missing"] == ["delay_pressure"]
    partial = next(line for line in result.caveats if "built from" in line)
    assert "80% of the score weight" in partial
    assert "fewer than 9 consecutive months of delay data" in partial
    assert "Not comparable" in partial


def test_a_full_score_has_no_partial_caveat(dataset: Dataset) -> None:
    result = _call(dataset, "BOS")

    assert not any("built from" in line for line in result.caveats)


def test_an_airport_no_reporting_carrier_serves_says_so(dataset: Dataset) -> None:
    result = _call(dataset, "HVN")

    partial = next(line for line in result.caveats if "built from" in line)
    assert "no reporting carrier serves it" in partial


def test_old_snapshots_are_flagged_stale_in_the_caveats(
    settings: Settings, repo: Path, offline_fetcher: HttpFetcher
) -> None:
    store = SnapshotStore(repo / settings.snapshot_dir)
    late = load_dataset(settings, store, offline_fetcher, today=date(2027, 6, 1))

    caveats = _call(late, "BOS").caveats

    assert any("stale: data month 2026-08 is 10 months old" in line for line in caveats)
    assert any("stale: data month 2026-07 is 11 months old" in line for line in caveats)


def test_without_delay_data_every_airport_says_so(dataset: Dataset) -> None:
    result = _call(dataclasses.replace(dataset, delays=None), "BOS")

    assert any("delay data is unavailable" in line for line in result.caveats)


def test_a_missing_traffic_component_is_explained_too(dataset: Dataset) -> None:
    assert "needs consecutive months of traffic" in _why_missing("load_factor", "BOS", dataset)


def test_the_score_comes_with_the_airports_passengers_and_a_size_caveat(dataset: Dataset) -> None:
    result = _call(dataset, "BOS")
    volume = dataset.passenger_volume["BOS"]

    assert result.data["passengers_12m"]["value"] == volume
    assert result.data["passengers_12m"]["shown"].endswith(" million")
    assert any("does not measure airport size" in c for c in result.caveats)
