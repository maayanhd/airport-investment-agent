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
