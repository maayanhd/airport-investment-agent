import io
import re
import zipfile
from pathlib import Path

import httpx
import pytest
import respx

from airport_agent.cli import main
from airport_agent.config import load_settings
from airport_agent.data.traffic import BTS_URL, SNAPSHOT_NAME

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
