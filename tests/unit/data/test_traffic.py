import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import respx

from airport_agent.data.http import HttpFetcher
from airport_agent.data.provider import DataProvider
from airport_agent.data.snapshots import SnapshotStore
from airport_agent.data.traffic import BTS_URL, load_traffic, parse_traffic
from airport_agent.domain import DataMode

SAMPLE = (Path(__file__).parents[2] / "fixtures" / "bts_t100_sample.json").read_text(
    encoding="utf-8"
)
ROWS: list[dict[str, Any]] = json.loads(SAMPLE)


def _without(column: str) -> str:
    return json.dumps([{k: v for k, v in r.items() if k != column} for r in ROWS])


def _with(column: str, value: str) -> str:
    return json.dumps([{**ROWS[0], column: value}])


BAD_CONTENT = [
    '{"error": true, "message": "query.soql.no-such-column"}',
    "[]",
    "<html>Service unavailable</html>",
    _without("total_seats"),
    _with("total_load_factor", "150"),
    _with("total_passengers", "many"),
    json.dumps([ROWS[0], ROWS[0]]),
]


def test_parse_real_response_into_typed_series() -> None:
    series = parse_traffic(SAMPLE, ["ANC", "BOS", "SNA", "LAX"])

    assert set(series) == {"ANC", "BOS", "SNA"}
    assert [m.month for m in series["ANC"]] == [date(2026, m, 1) for m in (4, 5, 6, 7)]

    july = series["ANC"][-1]
    assert (july.departures, july.passengers, july.seats) == (9087, 393566, 493315)
    assert july.load_factor == 79.8
    assert round(july.avg_flight_distance_miles) == 1625


def test_parse_ignores_airports_outside_coverage() -> None:
    assert set(parse_traffic(SAMPLE, ["ANC"])) == {"ANC"}


@pytest.mark.parametrize("bad_text", BAD_CONTENT)
def test_unusable_content_raises_only_value_error(bad_text: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        parse_traffic(bad_text, ["ANC", "BOS", "SNA"])


@respx.mock
def test_live_then_snapshot_fallback(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    fetcher = HttpFetcher(retries=0, sleep=lambda _: None)
    route = respx.get(BTS_URL).respond(200, text=SAMPLE)
    coverage = ["ANC", "BOS", "SNA", "LAX"]

    live = load_traffic(DataProvider(store), fetcher, coverage, months=24, today=date(2026, 8, 15))
    assert live.provenance.mode is DataMode.LIVE
    assert live.provenance.as_of == "2026-07"
    assert live.latest_month == date(2026, 7, 1)
    assert live.missing_codes == ["LAX"]

    where = route.calls.last.request.url.params["$where"]
    assert "'ANC','BOS','SNA','LAX'" in where
    assert "reporting_month >= '2024-08-01T00:00:00'" in where

    route.respond(500)
    offline = load_traffic(
        DataProvider(store), fetcher, coverage, months=24, today=date(2026, 8, 15)
    )
    assert offline.provenance.mode is DataMode.SNAPSHOT
    assert offline.provenance.as_of == "2026-07"
    assert offline.series("anc")[-1].passengers == 393566
    assert offline.series("JFK") == []


@respx.mock
def test_error_body_with_200_keeps_last_good_snapshot(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    fetcher = HttpFetcher(retries=0, sleep=lambda _: None)
    route = respx.get(BTS_URL).respond(200, text=SAMPLE)
    load_traffic(DataProvider(store), fetcher, ["ANC"], months=24)

    route.respond(200, text='{"error": true, "message": "boom"}')
    history = load_traffic(DataProvider(store), fetcher, ["ANC"], months=24)

    assert history.provenance.mode is DataMode.SNAPSHOT
    assert len(history.series("ANC")) == 4


@pytest.mark.parametrize(
    ("today", "since"), [(date(2026, 1, 10), "2024-01-01"), (date(2026, 12, 31), "2024-12-01")]
)
@respx.mock
def test_history_window_starts_on_first_of_month(tmp_path: Path, today: date, since: str) -> None:
    route = respx.get(BTS_URL).respond(200, text=SAMPLE)
    fetcher = HttpFetcher(retries=0, sleep=lambda _: None)

    load_traffic(DataProvider(SnapshotStore(tmp_path)), fetcher, ["ANC"], months=24, today=today)

    assert f">= '{since}T00:00:00'" in route.calls.last.request.url.params["$where"]


def test_rejects_non_iata_coverage(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="IATA"):
        load_traffic(DataProvider(SnapshotStore(tmp_path)), HttpFetcher(), ["A' OR 1=1"], months=24)
