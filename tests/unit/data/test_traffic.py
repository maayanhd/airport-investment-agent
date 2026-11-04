import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import respx

from airport_agent.config import Settings, load_settings
from airport_agent.data import traffic
from airport_agent.data.http import HttpFetcher
from airport_agent.data.provider import DataProvider
from airport_agent.data.snapshots import SnapshotStore
from airport_agent.data.traffic import BTS_URL, SNAPSHOT_NAME, load_traffic, parse_traffic
from airport_agent.domain import DataMode

SAMPLE = (Path(__file__).parents[2] / "fixtures" / "bts_t100_sample.json").read_text(
    encoding="utf-8"
)
ROWS: list[dict[str, Any]] = json.loads(SAMPLE)
COVERAGE = ["ANC", "BOS", "SNA", "LAX"]  # the sample has no LAX rows
TODAY = date(2026, 8, 15)

# The sample holds 4 months for 3 of these 4 airports, so the completeness rules are scaled to it.
SETTINGS = load_settings().model_copy(
    update={
        "coverage_airports": COVERAGE,
        "traffic_history_months": 24,
        "traffic_min_months": 4,
        "min_airport_share": 0.75,
    }
)


def _parse(text: str, coverage: list[str] = COVERAGE, *, min_months: int = 4) -> Any:
    return parse_traffic(text, coverage, min_months=min_months, min_airport_share=0.75)


def _without(column: str) -> str:
    return json.dumps([{k: v for k, v in r.items() if k != column} for r in ROWS])


def _with(column: str, value: str) -> str:
    return json.dumps([{**ROWS[0], column: value}])


def _only(*codes: str) -> str:
    return json.dumps([r for r in ROWS if r["origin_airport_code"] in codes])


def _load(
    store: SnapshotStore, settings: Settings = SETTINGS, *, persist: bool = True
) -> traffic.TrafficHistory:
    fetcher = HttpFetcher(retries=0, sleep=lambda _: None)

    return load_traffic(DataProvider(store, persist=persist), fetcher, settings, today=TODAY)


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
    series = _parse(SAMPLE)

    assert set(series) == {"ANC", "BOS", "SNA"}
    assert [m.month for m in series["ANC"]] == [date(2026, m, 1) for m in (4, 5, 6, 7)]

    july = series["ANC"][-1]
    assert (july.departures, july.passengers, july.seats) == (9087, 393566, 493315)
    assert july.load_factor == 79.8
    assert round(july.avg_flight_distance_miles) == 1625


def test_parse_ignores_airports_outside_coverage() -> None:
    assert set(_parse(SAMPLE, ["ANC"])) == {"ANC"}


@pytest.mark.parametrize("bad_text", BAD_CONTENT)
def test_unusable_content_raises_only_value_error(bad_text: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        _parse(bad_text, ["ANC", "BOS", "SNA"])


def test_bad_row_error_names_the_row_and_field() -> None:
    with pytest.raises(ValueError, match=r"0\.total_passengers"):
        _parse(_with("total_passengers", "many"))


def test_partial_response_is_rejected() -> None:
    with pytest.raises(ValueError, match="too thin"):
        _parse(_only("ANC"))


def test_too_few_months_is_rejected() -> None:
    with pytest.raises(ValueError, match="too thin"):
        _parse(SAMPLE, min_months=5)


def test_response_that_reached_the_row_limit_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(traffic, "MAX_ROWS", len(ROWS))

    with pytest.raises(ValueError, match="truncated"):
        _parse(SAMPLE)


@respx.mock
def test_live_then_snapshot_fallback(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    route = respx.get(BTS_URL).respond(200, text=SAMPLE)

    live = _load(store)
    assert live.provenance.mode is DataMode.LIVE
    assert live.provenance.as_of == "2026-07"
    assert live.missing_codes == ["LAX"]

    where = route.calls.last.request.url.params["$where"]
    assert "'ANC','BOS','SNA','LAX'" in where
    assert "reporting_month >= '2024-08-01T00:00:00'" in where

    route.respond(500)
    offline = _load(store)
    assert offline.provenance.mode is DataMode.SNAPSHOT
    assert offline.provenance.as_of == "2026-07"
    assert offline.series("anc")[-1].passengers == 393566
    assert offline.series("JFK") == []


@respx.mock
def test_error_body_with_200_keeps_last_good_snapshot(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    route = respx.get(BTS_URL).respond(200, text=SAMPLE)
    _load(store)

    route.respond(200, text='{"error": true, "message": "boom"}')
    history = _load(store)

    assert history.provenance.mode is DataMode.SNAPSHOT
    assert len(history.series("ANC")) == 4


@respx.mock
def test_partial_response_does_not_overwrite_the_full_snapshot(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    route = respx.get(BTS_URL).respond(200, text=SAMPLE)
    _load(store)

    route.respond(200, text=_only("ANC"))
    history = _load(store)

    assert history.provenance.mode is DataMode.SNAPSHOT
    assert "too thin" in history.provenance.note
    assert len(history.series("BOS")) == 4


@respx.mock
def test_persist_false_leaves_the_store_untouched(tmp_path: Path) -> None:
    respx.get(BTS_URL).respond(200, text=SAMPLE)
    store = SnapshotStore(tmp_path)

    history = _load(store, persist=False)

    assert history.provenance.mode is DataMode.LIVE
    assert store.load(SNAPSHOT_NAME) is None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("today", "since"), [(date(2026, 1, 10), "2024-01-01"), (date(2026, 12, 31), "2024-12-01")]
)
@respx.mock
def test_history_window_starts_on_first_of_month(tmp_path: Path, today: date, since: str) -> None:
    route = respx.get(BTS_URL).respond(200, text=SAMPLE)
    fetcher = HttpFetcher(retries=0, sleep=lambda _: None)

    load_traffic(DataProvider(SnapshotStore(tmp_path)), fetcher, SETTINGS, today=today)

    assert f">= '{since}T00:00:00'" in route.calls.last.request.url.params["$where"]


def test_default_window_leaves_room_for_the_bts_lag() -> None:
    settings = load_settings()

    # BTS publishes about 4 months late, so the window must exceed the months we need by that much.
    assert settings.traffic_history_months - 4 >= settings.traffic_min_months
