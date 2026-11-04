"""Live-first fetching with snapshot fallback, always reporting provenance."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from airport_agent.data.http import FetchError
from airport_agent.data.snapshots import SnapshotStore
from airport_agent.domain import DataMode, Provenance

logger = logging.getLogger(__name__)
MAX_NOTE_CHARS = 200


class DataUnavailableError(Exception):
    """Neither the live source nor a snapshot could provide the data."""


@dataclass(frozen=True)
class Fetched:
    text: str
    provenance: Provenance


class DataProvider:
    def __init__(self, store: SnapshotStore, *, persist_live: bool = True) -> None:
        self._store = store
        self._persist_live = persist_live

    def fetch_text(
        self,
        name: str,
        *,
        source: str,
        live: Callable[[], str],
        validate: Callable[[str], object] = lambda _: None,
        as_of: str = "",
    ) -> Fetched:
        """Live first. `validate` must raise ValueError on bad content, so a 200 response that is
        an error page or a truncated file never overwrites the last good snapshot."""
        try:
            text = live()
            validate(text)
        except (FetchError, ValueError) as error:
            reason = f"{type(error).__name__}: {error}"[:MAX_NOTE_CHARS]
            logger.warning("%s: live data rejected, using snapshot (%s)", source, reason)
            return self._from_snapshot(name, source, reason)
        if self._persist_live:
            self._store.save(name, text, source=source, as_of=as_of)
        provenance = Provenance(
            source=source, mode=DataMode.LIVE, fetched_at=datetime.now(UTC), as_of=as_of
        )
        return Fetched(text, provenance)

    def _from_snapshot(self, name: str, source: str, reason: str) -> Fetched:
        loaded = self._store.load(name)
        if loaded is None:
            raise DataUnavailableError(
                f"{source}: live data unusable ({reason}) and no valid snapshot for {name}"
            )
        text, entry = loaded
        provenance = Provenance(
            source=source,
            mode=DataMode.SNAPSHOT,
            fetched_at=entry.fetched_at,
            as_of=entry.as_of,
            note=f"live data unavailable, snapshot used ({reason})",
        )
        return Fetched(text, provenance)
