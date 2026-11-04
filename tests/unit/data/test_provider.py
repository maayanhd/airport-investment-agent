import logging
from pathlib import Path

import pytest

from airport_agent.data.http import FetchError
from airport_agent.data.provider import DataProvider, DataUnavailableError
from airport_agent.data.snapshots import SnapshotStore
from airport_agent.domain import DataMode


def failing() -> str:
    raise FetchError("down")


def must_start_with_h(text: str) -> str:
    if not text.startswith("h"):
        raise ValueError("not a csv")
    return text


class UnwritableStore(SnapshotStore):
    def save(self, *args: object, **kwargs: object) -> None:  # type: ignore[override]
        raise PermissionError("read-only folder")


def test_live_success_is_marked_live_and_persisted(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    result = DataProvider(store).fetch(
        "x.csv", source="S", live=lambda: "h\n1\n", parse=str, as_of=lambda _: "2026-07"
    )
    assert result.provenance.mode is DataMode.LIVE
    assert result.provenance.as_of == "2026-07"
    assert store.load("x.csv") is not None


def test_live_failure_falls_back_to_snapshot(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    store.save("x.csv", "h\n1\n", source="S", as_of="2026-06")
    result = DataProvider(store).fetch("x.csv", source="S", live=failing, parse=str)
    assert result.value == "h\n1\n"
    assert result.provenance.mode is DataMode.SNAPSHOT
    assert result.provenance.as_of == "2026-06"


def test_no_live_and_no_snapshot_raises(tmp_path: Path) -> None:
    with pytest.raises(DataUnavailableError):
        DataProvider(SnapshotStore(tmp_path)).fetch("x.csv", source="S", live=failing, parse=str)


def test_snapshot_that_no_longer_parses_is_unavailable_not_a_crash(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    store.save("x.csv", "<html>", source="S")
    with pytest.raises(DataUnavailableError, match="unreadable"):
        DataProvider(store).fetch("x.csv", source="S", live=failing, parse=must_start_with_h)


def test_invalid_live_content_does_not_overwrite_good_snapshot(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    store.save("x.csv", "h\n1\n", source="S", as_of="2026-06")
    result = DataProvider(store).fetch(
        "x.csv", source="S", live=lambda: "<html>maintenance</html>", parse=must_start_with_h
    )
    assert result.provenance.mode is DataMode.SNAPSHOT
    assert result.value == "h\n1\n"
    loaded = store.load("x.csv")
    assert loaded is not None
    assert loaded[0] == "h\n1\n"


def test_snapshot_fallback_records_the_reason(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    store.save("x.csv", "h\n1\n", source="S")
    result = DataProvider(store).fetch("x.csv", source="S", live=failing, parse=str)
    assert "snapshot used" in result.provenance.note
    assert "down" in result.provenance.note


def test_live_result_has_no_fallback_note(tmp_path: Path) -> None:
    result = DataProvider(SnapshotStore(tmp_path)).fetch(
        "x.csv", source="S", live=lambda: "a", parse=str
    )
    assert result.provenance.note == ""


def test_snapshot_write_failure_still_returns_live_data(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        result = DataProvider(UnwritableStore(tmp_path)).fetch(
            "x.csv", source="S", live=lambda: "h\n1\n", parse=str
        )
    assert result.provenance.mode is DataMode.LIVE
    assert result.value == "h\n1\n"
    assert "could not save snapshot" in caplog.text
