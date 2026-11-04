import io
import zipfile
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx
from pydantic import ValidationError

from airport_agent.config import Settings, load_settings
from airport_agent.data.delays import (
    URL,
    DelayHistory,
    _flag,
    aggregate_month,
    load_delays,
    parse_delays,
    refresh_delays,
)
from airport_agent.data.http import HttpFetcher
from airport_agent.data.provider import DataProvider, DataUnavailableError
from airport_agent.data.snapshots import SnapshotStore
from airport_agent.domain import DataMode, MonthlyDelays

CSV = (Path(__file__).parents[2] / "fixtures" / "ontime_sample.csv").read_text(encoding="utf-8")
JULY = date(2026, 7, 1)
SETTINGS = load_settings().model_copy(
    update={
        "coverage_airports": ["ANC", "BOS"],
        "delays_months": 2,
        "delays_min_months": 2,
        "min_airport_share": 1.0,
    }
)


def _zip(csv_text: str = CSV, name: str = "data.csv") -> bytes:
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, csv_text)

    return buffer.getvalue()


def _month_zip(year: int, month: int) -> bytes:
    return _zip(CSV.replace("2026-07-", f"{year}-{month:02d}-"))


def _url(year: int, month: int) -> str:
    return URL.format(year=year, month=month)


def _refresh(
    store: SnapshotStore, settings: Settings = SETTINGS, today: date = date(2026, 9, 10)
) -> DelayHistory:
    fetcher = HttpFetcher(retries=0, sleep=lambda _: None)

    return refresh_delays(DataProvider(store), fetcher, settings, today=today)


def test_aggregate_counts_a_real_excerpt_per_airport() -> None:
    by_airport = {d.iata: d for d in aggregate_month(_zip(), JULY, {"ANC", "BOS"})}

    assert set(by_airport) == {"ANC", "BOS"}  # XNA is not covered

    anc = by_airport["ANC"]
    assert (anc.flights, anc.cancelled, anc.delayed_15, anc.delay_minutes) == (4, 1, 1, 868.0)

    boston = by_airport["BOS"]
    assert (boston.flights, boston.cancelled, boston.delayed_15, boston.delay_minutes) == (
        3,
        1,
        1,
        54.0,  # the cancelled Boston flight also carries 28 delay minutes, which are ignored
    )


@pytest.mark.parametrize(
    "content",
    [
        b"<html>Service unavailable</html>",
        _zip(name="readme.html"),
        _zip(CSV.replace("DepDel15", "Other")),
        _month_zip(2026, 6),
    ],
)
def test_unusable_file_raises_only_value_error(content: bytes) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        aggregate_month(content, JULY, {"ANC", "BOS"})


def test_corrupt_zip_data_raises_value_error() -> None:
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("data.csv", CSV * 20)

    damaged = bytearray(buffer.getvalue())
    damaged[60:90] = bytes(30)

    with pytest.raises(ValueError, match="readable zip|CRC|Error"):
        aggregate_month(bytes(damaged), JULY, {"ANC"})


def test_ragged_row_raises_value_error() -> None:
    lines = CSV.rstrip("\n").split("\n")
    anc = next(i for i, line in enumerate(lines) if ',"ANC",' in line)
    lines[anc] = lines[anc][: len(lines[anc]) // 2]

    with pytest.raises(ValueError, match="ragged"):
        aggregate_month(_zip("\n".join(lines) + "\n"), JULY, {"ANC"})


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1.00", True), ("1", True), ("1.0", True), (" 1.00 ", True), ("0.00", False), ("0", False)],
)
def test_flag_accepts_any_numeric_spelling(value: str, expected: bool) -> None:
    assert _flag(value) is expected


@pytest.mark.parametrize("value", ["2.00", "yes", "", "-1"])
def test_flag_rejects_other_values(value: str) -> None:
    with pytest.raises(ValueError, match="unexpected flag"):
        _flag(value)


def test_flag_allows_blank_only_when_asked() -> None:
    assert _flag("", blank_ok=True) is False


def test_cancelled_and_delayed_cannot_exceed_flights() -> None:
    with pytest.raises(ValidationError, match="exceed"):
        MonthlyDelays(
            iata="ANC", month=JULY, flights=2, cancelled=1, delayed_15=2, delay_minutes=10.0
        )


def test_parse_rejects_a_response_that_is_too_thin() -> None:
    text = (
        '[{"iata":"ANC","month":"2026-07-01","flights":4,"cancelled":1,'
        '"delayed_15":1,"delay_minutes":868.0}]'
    )

    with pytest.raises(ValueError, match="too thin"):
        parse_delays(text, ["ANC", "BOS"], min_months=1, min_airport_share=0.9)

    with pytest.raises(ValueError, match="On-Time"):
        parse_delays("[{}]", ["ANC"], min_months=1, min_airport_share=0.9)


@respx.mock
def test_refresh_skips_unpublished_months_and_saves_a_snapshot(tmp_path: Path) -> None:
    respx.get(_url(2026, 8)).respond(404)
    respx.get(_url(2026, 7)).respond(200, content=_zip())
    respx.get(_url(2026, 6)).respond(200, content=_month_zip(2026, 6))

    history = _refresh(SnapshotStore(tmp_path))

    assert history.provenance.mode is DataMode.LIVE
    assert history.provenance.as_of == "2026-07"
    assert [d.month for d in history.series("anc")] == [date(2026, 6, 1), date(2026, 7, 1)]
    assert (tmp_path / "bts_ontime_delays.json").exists()


@respx.mock
def test_refresh_without_a_snapshot_reports_a_missing_middle_month(tmp_path: Path) -> None:
    respx.get(_url(2026, 8)).respond(200, content=_month_zip(2026, 8))
    respx.get(_url(2026, 7)).respond(404)

    with pytest.raises(DataUnavailableError, match="no valid snapshot"):
        _refresh(SnapshotStore(tmp_path))


@respx.mock
def test_failed_refresh_keeps_the_last_good_snapshot(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    respx.get(_url(2026, 8)).respond(200, content=_month_zip(2026, 8))
    respx.get(_url(2026, 7)).respond(200, content=_zip())
    _refresh(store)

    respx.get(_url(2026, 8)).mock(side_effect=httpx.ConnectError("down"))
    history = _refresh(store)

    assert history.provenance.mode is DataMode.SNAPSHOT
    assert "FetchError" in history.provenance.note
    assert history.provenance.as_of == "2026-08"


@pytest.mark.parametrize(
    ("today", "year", "month"), [(date(2026, 1, 10), 2025, 12), (date(2026, 11, 5), 2026, 10)]
)
@respx.mock
def test_first_requested_month_is_the_one_before_today(
    tmp_path: Path, today: date, year: int, month: int
) -> None:
    settings = SETTINGS.model_copy(update={"delays_months": 1, "delays_min_months": 1})
    route = respx.get(_url(year, month)).respond(200, content=_month_zip(year, month))

    _refresh(SnapshotStore(tmp_path), settings, today)

    assert route.called


@respx.mock
def test_load_reads_only_the_snapshot_and_never_the_network(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    respx.get(_url(2026, 8)).respond(200, content=_month_zip(2026, 8))
    respx.get(_url(2026, 7)).respond(200, content=_zip())
    _refresh(store)
    respx.clear()  # any request from here on would fail the test

    history = load_delays(DataProvider(store), SETTINGS)

    assert history.provenance.mode is DataMode.SNAPSHOT
    assert history.provenance.note == ""
    assert history.series("BOS")[-1].flights == 3


def test_load_without_a_snapshot_says_so(tmp_path: Path) -> None:
    with pytest.raises(DataUnavailableError, match="no valid snapshot"):
        load_delays(DataProvider(SnapshotStore(tmp_path)), SETTINGS)


@respx.mock
def test_old_snapshot_is_flagged_stale(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    respx.get(_url(2026, 8)).respond(200, content=_month_zip(2026, 8))
    respx.get(_url(2026, 7)).respond(200, content=_month_zip(2026, 7))
    _refresh(store)

    fresh = load_delays(DataProvider(store), SETTINGS, today=date(2027, 2, 1))
    old = load_delays(DataProvider(store), SETTINGS, today=date(2027, 3, 1))

    assert not fresh.provenance.stale
    assert old.provenance.stale
    assert "stale: data month 2026-08 is 7 months old (limit 6)" in old.provenance.note


@respx.mock
def test_airport_with_too_few_months_is_listed_as_short(tmp_path: Path) -> None:
    anc_only = "\n".join(
        line
        for line in CSV.rstrip("\n").split("\n")
        if line.startswith('"Year"') or '"ANC"' in line
    )
    respx.get(_url(2026, 8)).respond(200, content=_zip(anc_only.replace("2026-07-", "2026-08-")))
    respx.get(_url(2026, 7)).respond(200, content=_month_zip(2026, 7))
    settings = SETTINGS.model_copy(update={"min_airport_share": 0.5})

    history = _refresh(SnapshotStore(tmp_path), settings)

    assert history.short_codes == ["BOS"]
    assert len(history.series("ANC")) == 2
