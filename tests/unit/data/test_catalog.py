from pathlib import Path

import respx

from airport_agent.data.catalog import OURAIRPORTS_URL, OurAirportsCatalog, parse_airports
from airport_agent.data.http import HttpFetcher
from airport_agent.data.provider import DataProvider
from airport_agent.data.snapshots import SnapshotStore
from airport_agent.domain import DataMode

SAMPLE = (Path(__file__).parents[2] / "fixtures" / "airports_sample.csv").read_text(
    encoding="utf-8"
)


def test_parse_keeps_only_covered_us_airports_with_service_types() -> None:
    airports = parse_airports(SAMPLE, ["ANC", "BOS", "BDL", "LHR"])
    assert set(airports) == {"ANC", "BOS", "BDL"}  # LHR is not US; heliport row ignored
    assert airports["BOS"].name.startswith("General Edward")
    assert airports["ANC"].state == "AK"
    assert airports["BDL"].size == "medium"


@respx.mock
def test_catalog_live_then_snapshot_fallback(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    fetcher = HttpFetcher(retries=0, sleep=lambda _: None)
    route = respx.get(OURAIRPORTS_URL).respond(200, text=SAMPLE)

    live = OurAirportsCatalog(DataProvider(store), fetcher, ["ANC", "BOS", "BDL"])
    assert live.provenance.mode is DataMode.LIVE
    assert [a.iata for a in live.in_states(["MA", "CT"])] == ["BDL", "BOS"]

    route.respond(500)
    offline = OurAirportsCatalog(DataProvider(store), fetcher, ["ANC", "BOS", "BDL"])
    assert offline.provenance.mode is DataMode.SNAPSHOT
    assert offline.get("anc") is not None
    assert offline.get("JFK") is None  # not in coverage
