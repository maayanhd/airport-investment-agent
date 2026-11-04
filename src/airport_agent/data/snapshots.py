"""Pinned copies of fetched data, with a manifest and checksums."""

import hashlib
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)


class SnapshotEntry(BaseModel):
    source: str
    fetched_at: datetime
    as_of: str
    sha256: str


def _atomic_write(path: Path, data: bytes) -> None:
    temp = path.with_name(path.name + ".tmp")

    temp.write_bytes(data)
    os.replace(temp, path)


class SnapshotStore:
    MANIFEST = "manifest.json"

    def __init__(self, root: Path) -> None:
        self._root = root

    def save(self, name: str, text: str, *, source: str, as_of: str = "") -> SnapshotEntry:
        self._root.mkdir(parents=True, exist_ok=True)

        data = text.encode("utf-8")
        _atomic_write(self._root / name, data)

        entry = SnapshotEntry(
            source=source,
            fetched_at=datetime.now(UTC),
            as_of=as_of,
            sha256=hashlib.sha256(data).hexdigest(),
        )

        manifest = self._read_manifest()
        manifest[name] = entry
        self._write_manifest(manifest)

        return entry

    def entries(self) -> dict[str, SnapshotEntry]:
        return self._read_manifest()

    def load(self, name: str) -> tuple[str, SnapshotEntry] | None:
        entry = self._read_manifest().get(name)
        path = self._root / name

        if entry is None or not path.exists():
            return None

        data = path.read_bytes()

        if hashlib.sha256(data).hexdigest() != entry.sha256:
            return None

        return data.decode("utf-8"), entry

    def _read_manifest(self) -> dict[str, SnapshotEntry]:
        path = self._root / self.MANIFEST

        if not path.exists():
            return {}

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))

            return {name: SnapshotEntry.model_validate(item) for name, item in raw.items()}
        except (OSError, ValueError, AttributeError, ValidationError) as error:
            logger.warning("snapshot manifest unreadable, treating as empty (%s)", error)

            return {}

    def _write_manifest(self, manifest: dict[str, SnapshotEntry]) -> None:
        payload = {name: json.loads(entry.model_dump_json()) for name, entry in manifest.items()}
        text = json.dumps(payload, indent=2, sort_keys=True)

        _atomic_write(self._root / self.MANIFEST, text.encode("utf-8"))
