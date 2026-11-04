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


def _no_as_of(_value: object) -> str:
    return ""


class DataUnavailableError(Exception):
    """Neither the live source nor a snapshot could provide the data."""


@dataclass(frozen=True)
class Fetched(Generic[T]):
    value: T  # what `parse` returned, so callers never parse the text a second time
    provenance: Provenance


class DataProvider:
    def __init__(self, store: SnapshotStore, *, persist: bool = True) -> None:
        self._store = store
        self._persist = persist  # False makes the provider read-only

    def fetch(
        self,
        name: str,
        *,
        source: str,
        live: Callable[[], str],
        parse: Callable[[str], T],
        as_of: Callable[[T], str] = _no_as_of,
    ) -> Fetched[T]:
        """`parse` and `as_of` must raise ValueError on bad content, or it replaces the snapshot."""
        try:
            text = live()
            value = parse(text)
            data_month = as_of(value)
        except (FetchError, ValueError) as error:
            reason = f"{type(error).__name__}: {error}"[:MAX_NOTE_CHARS]
            logger.warning("%s: live data rejected, using snapshot", source)

            return self._from_snapshot(name, source, reason, parse)

        if self._persist:
            self._try_save(name, text, source, data_month)

        provenance = Provenance(
            source=source, mode=DataMode.LIVE, fetched_at=datetime.now(UTC), as_of=data_month
        )

        return Fetched(value, provenance)

    def _try_save(self, name: str, text: str, source: str, as_of: str) -> None:
        try:
            self._store.save(name, text, source=source, as_of=as_of)
        except OSError as error:
            logger.warning("%s: could not save snapshot %s (%s)", source, name, error)

    def read_snapshot(self, name: str, *, source: str, parse: Callable[[str], T]) -> Fetched[T]:
        """Snapshot only, for sources too big to download while serving a request."""
        return self._load(name, source, parse, reason=None)

    def _from_snapshot(
        self, name: str, source: str, reason: str, parse: Callable[[str], T]
    ) -> Fetched[T]:
        return self._load(name, source, parse, reason=reason)

    def _load(
        self, name: str, source: str, parse: Callable[[str], T], *, reason: str | None
    ) -> Fetched[T]:
        prefix = f"live data unusable ({reason}) and " if reason else ""
        note = f"live data unavailable, snapshot used ({reason})" if reason else ""
        loaded = self._store.load(name)

        if loaded is None:
            raise DataUnavailableError(f"{source}: {prefix}no valid snapshot for {name}")

        text, entry = loaded

        try:
            value = parse(text)
        except ValueError as error:
            raise DataUnavailableError(
                f"{source}: {prefix}snapshot {name} is unreadable"
            ) from error

        provenance = Provenance(
            source=source,
            mode=DataMode.SNAPSHOT,
            fetched_at=entry.fetched_at,
            as_of=entry.as_of,
            note=note,
        )

        return Fetched(value, provenance)
