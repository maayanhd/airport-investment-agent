"""Pinned copies of fetched data, with a manifest so every file has provenance."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel


class SnapshotEntry(BaseModel):
    source: str
    fetched_at: datetime
    as_of: str
    rows: int
    sha256: str


class SnapshotStore:
    MANIFEST = "manifest.json"

    def __init__(self, root: Path) -> None:
        self._root = root

    def save(self, name: str, text: str, *, source: str, as_of: str = "") -> SnapshotEntry:
        self._root.mkdir(parents=True, exist_ok=True)
        (self._root / name).write_text(text, encoding="utf-8", newline="\n")
        entry = SnapshotEntry(
            source=source,
            fetched_at=datetime.now(UTC),
            as_of=as_of,
            rows=max(text.count("\n") - 1, 0),
            sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )
        manifest = self._read_manifest()
        manifest[name] = entry
        self._write_manifest(manifest)
        return entry

    def load(self, name: str) -> tuple[str, SnapshotEntry] | None:
        entry = self._read_manifest().get(name)
        path = self._root / name
        if entry is None or not path.exists():
            return None
        return path.read_text(encoding="utf-8"), entry

    def _read_manifest(self) -> dict[str, SnapshotEntry]:
        path = self._root / self.MANIFEST
        if not path.exists():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {name: SnapshotEntry.model_validate(item) for name, item in raw.items()}

    def _write_manifest(self, manifest: dict[str, SnapshotEntry]) -> None:
        payload = {name: json.loads(entry.model_dump_json()) for name, entry in manifest.items()}
        (self._root / self.MANIFEST).write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8", newline="\n"
        )
