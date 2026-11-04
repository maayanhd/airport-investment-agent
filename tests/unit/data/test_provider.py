from pathlib import Path

import pytest

from airport_agent.data.http import FetchError
from airport_agent.data.provider import DataProvider, DataUnavailableError
from airport_agent.data.snapshots import SnapshotStore
from airport_agent.domain import DataMode


def failing() -> str:
    raise FetchError("down")


def test_live_success_is_marked_live_and_persisted(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    result = DataProvider(store).fetch_text(
        "x.csv", source="S", live=lambda: "h\n1\n", as_of="2026-07"
    )
    assert result.provenance.mode is DataMode.LIVE
    assert result.provenance.as_of == "2026-07"
    assert store.load("x.csv") is not None


def test_live_failure_falls_back_to_snapshot(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    store.save("x.csv", "h\n1\n", source="S", as_of="2026-06")
    result = DataProvider(store).fetch_text("x.csv", source="S", live=failing)
    assert result.text == "h\n1\n"
    assert result.provenance.mode is DataMode.SNAPSHOT
    assert result.provenance.as_of == "2026-06"


def test_no_live_and_no_snapshot_raises(tmp_path: Path) -> None:
    with pytest.raises(DataUnavailableError):
        DataProvider(SnapshotStore(tmp_path)).fetch_text("x.csv", source="S", live=failing)


def test_persist_live_can_be_disabled(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    DataProvider(store, persist_live=False).fetch_text("x.csv", source="S", live=lambda: "h\n1\n")
    assert store.load("x.csv") is None


def test_invalid_live_content_does_not_overwrite_good_snapshot(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    store.save("x.csv", "h\n1\n", source="S", as_of="2026-06")

    def reject(text: str) -> None:
        if not text.startswith("h"):
            raise ValueError("not a csv")

    result = DataProvider(store).fetch_text(
        "x.csv", source="S", live=lambda: "<html>maintenance</html>", validate=reject
    )
    assert result.provenance.mode is DataMode.SNAPSHOT
    assert result.text == "h\n1\n"
    loaded = store.load("x.csv")
    assert loaded is not None
    assert loaded[0] == "h\n1\n"


def test_snapshot_fallback_records_the_reason(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    store.save("x.csv", "h\n1\n", source="S")
    result = DataProvider(store).fetch_text("x.csv", source="S", live=failing)
    assert "snapshot used" in result.provenance.note
    assert "down" in result.provenance.note


def test_live_result_has_no_fallback_note(tmp_path: Path) -> None:
    result = DataProvider(SnapshotStore(tmp_path)).fetch_text("x.csv", source="S", live=lambda: "a")
    assert result.provenance.note == ""
