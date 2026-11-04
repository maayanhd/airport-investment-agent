from pathlib import Path

from airport_agent.data.snapshots import SnapshotStore


def test_save_then_load_roundtrip_with_manifest(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    entry = store.save("t.csv", "h\n1\n2\n", source="BTS", as_of="2026-07")
    loaded = store.load("t.csv")
    assert loaded is not None
    text, loaded_entry = loaded
    assert text == "h\n1\n2\n"
    assert loaded_entry == entry
    assert entry.rows == 2
    assert entry.as_of == "2026-07"
    assert len(entry.sha256) == 64


def test_load_missing_returns_none(tmp_path: Path) -> None:
    assert SnapshotStore(tmp_path).load("nope.csv") is None


def test_manifest_keeps_other_entries(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    store.save("a.csv", "h\n1\n", source="A")
    store.save("b.csv", "h\n1\n", source="B")
    assert store.load("a.csv") is not None
    assert store.load("b.csv") is not None
