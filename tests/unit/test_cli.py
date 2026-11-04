from pathlib import Path

import pytest
import respx

from airport_agent.cli import main
from airport_agent.data.traffic import BTS_URL

SAMPLE = (Path(__file__).parents[1] / "fixtures" / "bts_t100_sample.json").read_text(
    encoding="utf-8"
)


@respx.mock
def test_show_prints_provenance_and_recent_months(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    respx.get(BTS_URL).respond(200, text=SAMPLE)

    assert main(["data", "show", "anc"], root=tmp_path) == 0

    out = capsys.readouterr().out
    assert "ANC | BTS T-100" in out
    assert "| live | data month 2026-07" in out
    assert "2026-07" in out
    assert "393566" in out


@respx.mock
def test_status_lists_the_snapshot_saved_by_show(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    respx.get(BTS_URL).respond(200, text=SAMPLE)
    main(["data", "show", "ANC"], root=tmp_path)
    capsys.readouterr()

    assert main(["data", "status"], root=tmp_path) == 0
    assert "bts_t100_traffic.json: BTS T-100" in capsys.readouterr().out


def test_status_without_snapshots(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["data", "status"], root=tmp_path) == 0
    assert "no snapshots yet" in capsys.readouterr().out


def test_show_rejects_airport_outside_coverage(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["data", "show", "XXX"], root=tmp_path) == 2
    assert "not in the coverage list" in capsys.readouterr().out


@respx.mock
def test_show_reports_when_no_data_at_all(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    respx.get(BTS_URL).respond(503)

    assert main(["data", "show", "ANC"], root=tmp_path) == 1
    assert "no valid snapshot" in capsys.readouterr().out


@respx.mock
def test_show_prints_snapshot_note_when_live_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    route = respx.get(BTS_URL).respond(200, text=SAMPLE)
    main(["data", "show", "ANC"], root=tmp_path)
    capsys.readouterr()

    route.respond(503)
    assert main(["data", "show", "ANC"], root=tmp_path) == 0

    out = capsys.readouterr().out
    assert "| snapshot | data month 2026-07" in out
    assert "note: live data unavailable" in out


@respx.mock
def test_show_reports_covered_airport_missing_from_data(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    respx.get(BTS_URL).respond(200, text=SAMPLE)

    assert main(["data", "show", "LAX"], root=tmp_path) == 3
    assert "LAX has no rows" in capsys.readouterr().out
