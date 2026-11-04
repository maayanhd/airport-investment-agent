from pathlib import Path

import pytest
import respx

from airport_agent.data.catalog import OURAIRPORTS_URL, load_catalog, parse_airports
from airport_agent.data.http import HttpFetcher
from airport_agent.data.provider import DataProvider
from airport_agent.data.snapshots import SnapshotStore
from airport_agent.domain import DataMode

SAMPLE = (Path(__file__).parents[2] / "fixtures" / "airports_sample.csv").read_text(
    encoding="utf-8"
)
TRUNCATED = SAMPLE.rsplit("\n", 3)[0] + '\n3,"KBDL","large_airport","Bradley"\n'
BAD_CONTENT = [
    TRUNCATED,
    SAMPLE.replace("latitude_deg", "lat"),  # a used column is gone
    SAMPLE.replace('"US-AK","Anchorage"', '"US-AK"'),  # ragged row
    "<html>Service unavailable</html>",
]


def test_parse_keeps_only_covered_us_airports_with_service_types() -> None:
    airports = parse_airports(SAMPLE, ["ANC", "BOS", "BDL", "LHR", "WLR"])
    # LHR is not US, WLR is a seaplane base, and the Argentine "Lobos" also has code BOS
    assert set(airports) == {"ANC", "BOS", "BDL"}
    assert airports["BOS"].state == "MA"
    assert airports["BOS"].name.startswith("Boston Logan")
    assert airports["ANC"].state == "AK"
    assert airports["BDL"].size == "large"


@respx.mock
def test_catalog_live_then_snapshot_fallback(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    fetcher = HttpFetcher(retries=0, sleep=lambda _: None)
    route = respx.get(OURAIRPORTS_URL).respond(200, text=SAMPLE)

    live = load_catalog(DataProvider(store), fetcher, ["ANC", "BOS", "BDL"])
    assert live.provenance.mode is DataMode.LIVE
    assert [a.iata for a in live.in_states(["MA", "CT"])] == ["BDL", "BOS"]

    route.respond(500)
    offline = load_catalog(DataProvider(store), fetcher, ["ANC", "BOS", "BDL"])
    assert offline.provenance.mode is DataMode.SNAPSHOT
    assert offline.get("anc") is not None
    assert offline.get("JFK") is None  # not in coverage


@respx.mock
def test_error_page_with_200_keeps_last_good_snapshot(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    fetcher = HttpFetcher(retries=0, sleep=lambda _: None)
    route = respx.get(OURAIRPORTS_URL).respond(200, text=SAMPLE)
    load_catalog(DataProvider(store), fetcher, ["ANC", "BOS"])

    route.respond(200, text="<html>Service unavailable</html>")
    catalog = load_catalog(DataProvider(store), fetcher, ["ANC", "BOS"])

    assert catalog.provenance.mode is DataMode.SNAPSHOT
    assert catalog.get("ANC") is not None


@pytest.mark.parametrize("bad_text", BAD_CONTENT)
def test_malformed_content_raises_only_value_error(bad_text: str) -> None:
    with pytest.raises(ValueError, match="airports|OurAirports|covered"):
        parse_airports(bad_text, ["ANC", "BOS", "BDL"])


def test_no_covered_airport_in_file_raises_value_error() -> None:
    with pytest.raises(ValueError, match="no covered airport"):
        parse_airports(SAMPLE, ["SFO"])  # SFO is not in the excerpt


@respx.mock
def test_truncated_live_file_falls_back_to_snapshot(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    fetcher = HttpFetcher(retries=0, sleep=lambda _: None)
    route = respx.get(OURAIRPORTS_URL).respond(200, text=SAMPLE)
    load_catalog(DataProvider(store), fetcher, ["ANC", "BOS"])

    route.respond(200, text=TRUNCATED)
    catalog = load_catalog(DataProvider(store), fetcher, ["ANC", "BOS"])
    assert catalog.provenance.mode is DataMode.SNAPSHOT
    assert "snapshot used" in catalog.provenance.note
    assert catalog.get("BOS") is not None


@respx.mock
def test_codes_missing_from_data_are_reported(tmp_path: Path) -> None:
    respx.get(OURAIRPORTS_URL).respond(200, text=SAMPLE)
    fetcher = HttpFetcher(retries=0, sleep=lambda _: None)
    catalog = load_catalog(DataProvider(SnapshotStore(tmp_path)), fetcher, ["ANC", "ZZZ"])
    assert catalog.missing_codes == ["ZZZ"]
    assert catalog.get("ZZZ") is None
