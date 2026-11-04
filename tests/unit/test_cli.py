from pathlib import Path

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
        "traffic_min_airport_share": 0.75,
    }
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
