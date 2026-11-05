"""Everything the tools read, loaded once so every answer in a conversation is consistent."""

from dataclasses import dataclass
from datetime import date

from airport_agent.config import Settings
from airport_agent.data.catalog import OurAirportsCatalog, load_catalog
from airport_agent.data.delays import DelayHistory, load_delays
from airport_agent.data.http import HttpFetcher
from airport_agent.data.provider import DataProvider, DataUnavailableError
from airport_agent.data.snapshots import SnapshotStore
from airport_agent.data.traffic import TrafficHistory, load_traffic
from airport_agent.domain import Provenance


@dataclass(frozen=True)
class Dataset:
    settings: Settings
    catalog: OurAirportsCatalog
    traffic: TrafficHistory
    delays: DelayHistory | None  # None when no delay snapshot exists
    warnings: tuple[str, ...] = ()

    def sources(self) -> list[Provenance]:
        sources = [self.catalog.provenance, self.traffic.provenance]

        if self.delays:
            sources.append(self.delays.provenance)

        return sources


def load_dataset(
    settings: Settings, store: SnapshotStore, fetcher: HttpFetcher, *, today: date | None = None
) -> Dataset:
    """Writes no snapshot. Catalog and delays are read from snapshots; traffic is fetched live
    first, with its snapshot as the fallback. Raises DataUnavailableError if either is missing."""
    provider = DataProvider(store, persist=False)
    catalog = load_catalog(
        provider, settings.coverage_airports, min_airport_share=settings.min_airport_share
    )
    traffic = load_traffic(provider, fetcher, settings, today=today)

    try:
        delays: DelayHistory | None = load_delays(provider, settings, today=today)
        warnings: tuple[str, ...] = ()
    except DataUnavailableError as error:
        delays = None
        warnings = (
            f"delay data unavailable, so delay_pressure is missing for every airport: {error}",
        )

    return Dataset(settings, catalog, traffic, delays, warnings)
