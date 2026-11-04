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
    assert entry.lines == 2
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


def test_tampered_snapshot_is_not_served(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    store.save("t.csv", "h\n1\n", source="BTS")
    (tmp_path / "t.csv").write_text("h\n999\n", encoding="utf-8")
    assert store.load("t.csv") is None


def test_corrupt_manifest_is_treated_as_empty_and_repaired_on_next_save(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    store.save("a.csv", "h\n1\n", source="A")
    (tmp_path / "manifest.json").write_text('{"a.csv": {"sour', encoding="utf-8")
    assert store.load("a.csv") is None  # no crash
    store.save("b.csv", "h\n2\n", source="B")
    assert store.load("b.csv") is not None


def test_crlf_content_roundtrips_with_matching_checksum(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    store.save("t.csv", "h\r\n1\r\n", source="BTS")
    loaded = store.load("t.csv")
    assert loaded is not None
    assert loaded[0] == "h\r\n1\r\n"


def test_save_leaves_no_temp_files(tmp_path: Path) -> None:
    SnapshotStore(tmp_path).save("t.csv", "h\n1\n", source="BTS")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["manifest.json", "t.csv"]
