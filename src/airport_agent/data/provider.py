"""Live-first fetching with snapshot fallback, always reporting provenance."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Generic, TypeVar

from airport_agent.data.http import FetchError
from airport_agent.data.snapshots import SnapshotStore
from airport_agent.domain import DataMode, Provenance

logger = logging.getLogger(__name__)
MAX_NOTE_CHARS = 200
T = TypeVar("T")


class DataUnavailableError(Exception):
    """Neither the live source nor a snapshot could provide the data."""


@dataclass(frozen=True)
class Fetched(Generic[T]):
    value: T  # what `parse` returned, so callers never parse the text a second time
    provenance: Provenance


class DataProvider:
    def __init__(self, store: SnapshotStore) -> None:
        self._store = store

    def fetch(
        self,
        name: str,
        *,
        source: str,
        live: Callable[[], str],
        parse: Callable[[str], T],
        as_of: str = "",
    ) -> Fetched[T]:
        """Live first. `parse` must raise ValueError on bad content, so a 200 response that is
        an error page or a truncated file never overwrites the last good snapshot."""
        try:
            text = live()
            value = parse(text)
        except (FetchError, ValueError) as error:
            reason = f"{type(error).__name__}: {error}"[:MAX_NOTE_CHARS]
            logger.warning("%s: live data rejected, using snapshot (%s)", source, reason)

            return self._from_snapshot(name, source, reason, parse)

        self._try_save(name, text, source, as_of)

        provenance = Provenance(
            source=source, mode=DataMode.LIVE, fetched_at=datetime.now(UTC), as_of=as_of
        )

        return Fetched(value, provenance)

    def _try_save(self, name: str, text: str, source: str, as_of: str) -> None:
        """A failed write must not discard good live data; it only loses the fallback copy."""
        try:
            self._store.save(name, text, source=source, as_of=as_of)
        except OSError as error:
            logger.warning("%s: could not save snapshot %s (%s)", source, name, error)

    def _from_snapshot(
        self, name: str, source: str, reason: str, parse: Callable[[str], T]
    ) -> Fetched[T]:
        loaded = self._store.load(name)

        if loaded is None:
            raise DataUnavailableError(
                f"{source}: live data unusable ({reason}) and no valid snapshot for {name}"
            )

        text, entry = loaded

        try:
            value = parse(text)
        except ValueError as error:
            raise DataUnavailableError(
                f"{source}: live data unusable ({reason}) and snapshot {name} is unreadable"
            ) from error

        provenance = Provenance(
            source=source,
            mode=DataMode.SNAPSHOT,
            fetched_at=entry.fetched_at,
            as_of=entry.as_of,
            note=f"live data unavailable, snapshot used ({reason})",
        )

        return Fetched(value, provenance)
