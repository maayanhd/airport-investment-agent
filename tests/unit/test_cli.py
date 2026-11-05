import io
import json
import re
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from airport_agent import cli
from airport_agent.cli import main
from airport_agent.config import load_settings
from airport_agent.data.history import History
from airport_agent.data.http import HttpFetcher
from airport_agent.data.snapshots import SnapshotStore
from airport_agent.data.traffic import BTS_URL, SNAPSHOT_NAME
from airport_agent.domain import DataMode, Provenance

SAMPLE = (Path(__file__).parents[1] / "fixtures" / "bts_t100_sample.json").read_text(
    encoding="utf-8"
)
SETTINGS = load_settings().model_copy(
    update={
        "coverage_airports": ["ANC", "BOS", "SNA", "LAX"],
        "traffic_history_months": 24,
        "traffic_min_months": 4,
        "min_airport_share": 0.75,
    }
)
WITH_DELAYS = SETTINGS.model_copy(
    update={"delays_months": 2, "delays_min_months": 2, "min_airport_share": 0.5}
)
ONTIME_CSV = (Path(__file__).parents[1] / "fixtures" / "ontime_sample.csv").read_text(
    encoding="utf-8"
)
Out = pytest.CaptureFixture[str]


def _run(*argv: str, root: Path) -> int:
    return main(["data", *argv], root=root, settings=SETTINGS)


def _snapshot_files(root: Path) -> list[Path]:
    folder = root / SETTINGS.snapshot_dir

    return list(folder.glob("*")) if folder.exists() else []


@respx.mock
def test_show_prints_provenance_and_airports_own_month(tmp_path: Path, capsys: Out) -> None:
    respx.get(BTS_URL).respond(200, text=SAMPLE)

    assert _run("show", "anc", root=tmp_path) == 0

    out = capsys.readouterr().out
    assert "ANC | BTS T-100" in out
    assert "| live | ANC data through 2026-07 (dataset through 2026-07)" in out
    assert "393566" in out


@respx.mock
def test_show_is_read_only(tmp_path: Path) -> None:
    respx.get(BTS_URL).respond(200, text=SAMPLE)

    _run("show", "ANC", root=tmp_path)

    assert _snapshot_files(tmp_path) == []


@respx.mock
def test_refresh_saves_and_status_lists_it(tmp_path: Path, capsys: Out) -> None:
    respx.get(BTS_URL).respond(200, text=SAMPLE)

    assert _run("refresh", root=tmp_path) == 0
    assert "covered but not in the data: LAX" in capsys.readouterr().out

    assert _run("status", root=tmp_path) == 0
    assert f"{SNAPSHOT_NAME}: BTS T-100" in capsys.readouterr().out


@respx.mock
def test_refresh_fails_loudly_when_live_is_unusable(tmp_path: Path, capsys: Out) -> None:
    route = respx.get(BTS_URL).respond(200, text=SAMPLE)
    _run("refresh", root=tmp_path)
    capsys.readouterr()

    route.respond(503)

    assert _run("refresh", root=tmp_path) == 1
    assert "refresh failed, snapshot unchanged" in capsys.readouterr().out


def test_status_without_snapshots(tmp_path: Path, capsys: Out) -> None:
    assert _run("status", root=tmp_path) == 0
    assert "no snapshots yet" in capsys.readouterr().out


@respx.mock
def test_status_flags_a_snapshot_with_a_bad_checksum(tmp_path: Path, capsys: Out) -> None:
    respx.get(BTS_URL).respond(200, text=SAMPLE)
    _run("refresh", root=tmp_path)
    capsys.readouterr()
    (tmp_path / SETTINGS.snapshot_dir / SNAPSHOT_NAME).write_text("tampered", encoding="utf-8")

    assert _run("status", root=tmp_path) == 1
    assert "INVALID" in capsys.readouterr().out


def test_show_rejects_airport_outside_coverage(tmp_path: Path, capsys: Out) -> None:
    assert _run("show", "XXX", root=tmp_path) == 2
    assert "not in the coverage list" in capsys.readouterr().out


@respx.mock
def test_show_reports_when_no_data_at_all(tmp_path: Path, capsys: Out) -> None:
    respx.get(BTS_URL).respond(503)

    assert _run("show", "ANC", root=tmp_path) == 1
    assert "no valid snapshot" in capsys.readouterr().out


@respx.mock
def test_show_prints_snapshot_note_when_live_fails(tmp_path: Path, capsys: Out) -> None:
    route = respx.get(BTS_URL).respond(200, text=SAMPLE)
    _run("refresh", root=tmp_path)
    capsys.readouterr()

    route.respond(503)
    assert _run("show", "ANC", root=tmp_path) == 0

    out = capsys.readouterr().out
    assert "| snapshot | ANC data through 2026-07" in out
    assert "note: live data unavailable" in out


@respx.mock
def test_show_reports_covered_airport_missing_from_data(tmp_path: Path, capsys: Out) -> None:
    respx.get(BTS_URL).respond(200, text=SAMPLE)

    assert _run("show", "LAX", root=tmp_path) == 3
    assert "LAX has no rows" in capsys.readouterr().out


def _ontime_zip(request: httpx.Request) -> httpx.Response:
    found = re.search(r"_(\d{4})_(\d{1,2})\.zip", str(request.url))
    assert found
    year, month = found.groups()
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("data.csv", ONTIME_CSV.replace("2026-07-", f"{year}-{int(month):02d}-"))

    return httpx.Response(200, content=buffer.getvalue())


def test_show_points_to_refresh_when_there_is_no_delay_snapshot(
    tmp_path: Path, capsys: Out
) -> None:
    with respx.mock:
        respx.get(BTS_URL).respond(200, text=SAMPLE)
        assert main(["data", "show", "ANC"], root=tmp_path, settings=WITH_DELAYS) == 0

    assert "no snapshot yet, run `airport-agent data refresh delays`" in capsys.readouterr().out


@respx.mock
def test_refresh_delays_then_show_prints_both_tables(tmp_path: Path, capsys: Out) -> None:
    respx.get(url__regex=r".*PREZIP.*\.zip").mock(side_effect=_ontime_zip)
    respx.get(BTS_URL).respond(200, text=SAMPLE)

    assert main(["data", "refresh", "delays"], root=tmp_path, settings=WITH_DELAYS) == 0
    assert "airports with data: 2" in capsys.readouterr().out

    assert main(["data", "show", "ANC"], root=tmp_path, settings=WITH_DELAYS) == 0

    out = capsys.readouterr().out
    assert "393566" in out  # the traffic table
    assert "delays | BTS On-Time Reporting" in out
    assert "late15%" in out
    assert "25.0" in out  # ANC: 1 of 4 flights cancelled
    assert "33.3" in out  # ANC: 1 of the 3 operated flights was 15+ minutes late
    assert "289.3" in out  # ANC: 868 delay minutes over 3 operated flights


@respx.mock
def test_refresh_delays_fails_loudly_without_a_snapshot(tmp_path: Path, capsys: Out) -> None:
    respx.get(url__regex=r".*PREZIP.*\.zip").respond(503)

    assert main(["data", "refresh", "delays"], root=tmp_path, settings=WITH_DELAYS) == 1
    assert "no valid snapshot" in capsys.readouterr().out


@respx.mock
def test_show_says_when_an_airport_has_no_on_time_rows(tmp_path: Path, capsys: Out) -> None:
    respx.get(url__regex=r".*PREZIP.*\.zip").mock(side_effect=_ontime_zip)
    respx.get(BTS_URL).respond(200, text=SAMPLE)
    main(["data", "refresh", "delays"], root=tmp_path, settings=WITH_DELAYS)
    capsys.readouterr()

    assert main(["data", "show", "SNA"], root=tmp_path, settings=WITH_DELAYS) == 0
    assert "delays: no On-Time rows for SNA" in capsys.readouterr().out


@respx.mock
def test_show_still_prints_delays_when_traffic_is_unavailable(tmp_path: Path, capsys: Out) -> None:
    respx.get(url__regex=r".*PREZIP.*\.zip").mock(side_effect=_ontime_zip)
    main(["data", "refresh", "delays"], root=tmp_path, settings=WITH_DELAYS)
    capsys.readouterr()
    respx.get(BTS_URL).respond(503)

    assert main(["data", "show", "ANC"], root=tmp_path, settings=WITH_DELAYS) == 1

    out = capsys.readouterr().out
    assert "no valid snapshot" in out
    assert "late15%" in out


def _ontime_zip_without_boston_in_newest_month() -> Callable[[httpx.Request], httpx.Response]:
    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        response = _ontime_zip(request)
        calls.append(str(request.url))

        if len(calls) > 1:  # the newest month is requested first
            return response

        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            lines = archive.read("data.csv").decode().splitlines()

        buffer = io.BytesIO()

        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("data.csv", "\n".join(x for x in lines if '"BOS"' not in x) + "\n")

        return httpx.Response(200, content=buffer.getvalue())

    return respond


@respx.mock
def test_show_warns_about_an_airport_with_too_few_months(tmp_path: Path, capsys: Out) -> None:
    respx.get(url__regex=r".*PREZIP.*\.zip").mock(
        side_effect=_ontime_zip_without_boston_in_newest_month()
    )
    respx.get(BTS_URL).respond(200, text=SAMPLE)
    settings = WITH_DELAYS.model_copy(update={"coverage_airports": ["ANC", "BOS"]})
    main(["data", "refresh", "delays"], root=tmp_path, settings=settings)
    capsys.readouterr()

    main(["data", "show", "BOS"], root=tmp_path, settings=settings)

    assert "note: only 1 month(s) for BOS (need 2)" in capsys.readouterr().out


def test_unknown_refresh_source_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["data", "refresh", "weather"], root=tmp_path, settings=SETTINGS)


def test_refresh_warns_when_even_the_fresh_data_is_stale(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale = Provenance(
        source="S",
        mode=DataMode.LIVE,
        fetched_at=datetime.now(UTC),
        as_of="2025-01",
        note="stale: data month 2025-01 is 21 months old (limit 6)",
        stale=True,
    )
    history: History[object] = History({"ANC": []}, stale, ["ANC"])
    monkeypatch.setitem(cli.REFRESHERS, cli.RefreshSource.DELAYS, lambda *_: history)

    assert main(["data", "refresh", "delays"], root=tmp_path, settings=SETTINGS) == 0
    assert "warning: stale: data month 2025-01" in capsys.readouterr().out


@respx.mock
def test_refresh_fails_when_the_snapshot_cannot_be_written(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    respx.get(BTS_URL).respond(200, text=SAMPLE)

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("read-only folder")

    monkeypatch.setattr(SnapshotStore, "save", refuse)

    assert _run("refresh", root=tmp_path) == 1

    output = capsys.readouterr().out

    assert "could not write the snapshot" in output
    assert "saved BTS" not in output


@respx.mock
def test_status_marks_an_old_snapshot_stale(tmp_path: Path, capsys: Out) -> None:
    respx.get(BTS_URL).respond(200, text=SAMPLE)
    _run("refresh", root=tmp_path)
    capsys.readouterr()
    old = SETTINGS.model_copy(update={"max_data_age_months": 0})

    assert main(["data", "status"], root=tmp_path, settings=old) == 0
    assert "STALE" in capsys.readouterr().out


def _two_years_of_traffic() -> str:
    rows = []

    for i in range(26):
        index = 2024 * 12 + 5 + i
        rows.append(
            {
                "origin_airport_code": "ANC",
                "reporting_month": f"{index // 12}-{index % 12 + 1:02d}-01T00:00:00.000",
                "total_departures": "100",
                "total_passengers": str(1000 + 10 * i),
                "total_seats": "2000",
                "total_load_factor": "50.0",
                "total_distance_flight_sm": "500",
            }
        )

    return json.dumps(rows)


@respx.mock
def test_score_prints_components_and_what_is_missing(tmp_path: Path, capsys: Out) -> None:
    respx.get(BTS_URL).respond(200, text=_two_years_of_traffic())
    only_anc = SETTINGS.model_copy(update={"coverage_airports": ["ANC"]})

    assert main(["score", "anc"], root=tmp_path, settings=only_anc) == 0

    out = capsys.readouterr().out
    assert "ANC | score " in out
    assert "confidence 80%" in out  # no delay snapshot, so 80% of the weight has data
    assert "passenger_growth" in out
    assert "missing (not enough data): delay_pressure" in out
    assert "data: delays unavailable" in out
    assert "data: traffic live" in out


@respx.mock
def test_score_says_when_there_is_too_little_history(tmp_path: Path, capsys: Out) -> None:
    respx.get(BTS_URL).respond(200, text=SAMPLE)  # only 4 months

    assert main(["score", "ANC"], root=tmp_path, settings=SETTINGS) == 0

    out = capsys.readouterr().out
    assert "ANC | no score" in out
    assert "need 60%" in out


def test_score_rejects_airports_outside_coverage(tmp_path: Path, capsys: Out) -> None:
    assert main(["score", "ANC", "XXX"], root=tmp_path, settings=SETTINGS) == 2
    assert "not in the coverage list: XXX" in capsys.readouterr().out


@respx.mock
def test_score_reports_when_traffic_data_is_unavailable(tmp_path: Path, capsys: Out) -> None:
    respx.get(BTS_URL).respond(503)

    assert main(["score", "ANC"], root=tmp_path, settings=SETTINGS) == 1
    assert "no valid snapshot" in capsys.readouterr().out


@respx.mock
def test_score_lists_a_repeated_airport_once(tmp_path: Path, capsys: Out) -> None:
    respx.get(BTS_URL).respond(200, text=_two_years_of_traffic())
    only_anc = SETTINGS.model_copy(update={"coverage_airports": ["ANC"]})

    assert main(["score", "ANC", "anc", "ANC"], root=tmp_path, settings=only_anc) == 0
    assert capsys.readouterr().out.count("ANC | score ") == 1


def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """The committed snapshots are the data; traffic's live call fails fast and falls back."""
    monkeypatch.setattr(cli, "HttpFetcher", lambda: HttpFetcher(retries=0, sleep=lambda _: None))


def _tool(*argv: str, capsys: Out) -> tuple[int, str]:
    status = main(["tool", *argv], root=Path(__file__).parents[2], settings=load_settings())

    return status, capsys.readouterr().out


def test_tool_without_a_name_lists_the_tools_without_any_data(tmp_path: Path, capsys: Out) -> None:
    status = main(["tool"], root=tmp_path, settings=load_settings())  # no snapshots here

    assert status == 0
    assert capsys.readouterr().out.startswith("airport_score: Expansion score")


@respx.mock
def test_tool_runs_like_the_agent_would_and_prints_json(
    capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    respx.get(BTS_URL).respond(503)
    _offline(monkeypatch)

    status, out = _tool("airport_score", '{"airport": "LA"}', capsys=capsys)
    result = json.loads(out)

    assert status == 0
    assert result["data"]["airport"]["iata"] == "LAX"
    assert result["sources"]


@respx.mock
def test_tool_failures_exit_non_zero_with_the_reason(
    capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    respx.get(BTS_URL).respond(503)
    _offline(monkeypatch)

    status, out = _tool("airport_score", '{"airport": "Portland"}', capsys=capsys)

    assert status == 1
    assert json.loads(out)["error"]["options"] == ["PDX", "PWM"]

    status, out = _tool("nope", capsys=capsys)

    assert (status, json.loads(out)["error"]["kind"]) == (1, "unknown_tool")


@respx.mock
def test_tool_rejects_arguments_that_are_not_json(
    capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    respx.get(BTS_URL).respond(503)
    _offline(monkeypatch)

    status, out = _tool("airport_score", "not json", capsys=capsys)

    assert status == 2
    assert "arguments must be JSON" in out


def test_tool_reports_missing_data_instead_of_crashing(tmp_path: Path, capsys: Out) -> None:
    assert main(["tool", "airport_score"], root=tmp_path, settings=load_settings()) == 1
    assert "no valid snapshot" in capsys.readouterr().out


@respx.mock
@pytest.mark.parametrize("arguments", ['"BOS"', "[1, 2]", "not json"])
def test_tool_rejects_bad_arguments_before_loading_any_data(arguments: str, capsys: Out) -> None:
    status, out = _tool("airport_score", arguments, capsys=capsys)  # respx fails on any request

    assert status == 2
    assert "arguments must be" in out
