"""Everything the tools read, loaded once so every answer in a conversation is consistent."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from functools import cached_property

from airport_agent.config import Settings
from airport_agent.data.catalog import OurAirportsCatalog, load_catalog
from airport_agent.data.delays import DelayHistory, load_delays
from airport_agent.data.http import HttpFetcher
from airport_agent.data.provider import DataProvider, DataUnavailableError
from airport_agent.data.snapshots import SnapshotStore
from airport_agent.data.traffic import TrafficHistory, load_traffic
from airport_agent.domain import Provenance, search_words

YEAR_MONTHS = 12


@dataclass(frozen=True)
class Dataset:
    settings: Settings
    catalog: OurAirportsCatalog
    traffic: TrafficHistory
    delays: DelayHistory | None  # None when no delay snapshot exists
    warnings: tuple[str, ...] = ()

    def passengers_12m(self, code: str) -> int | None:
        """Passengers in the airport's newest 12 months, or None without a full year of data."""
        year = self.traffic.series(code)[-YEAR_MONTHS:]

        return sum(m.passengers for m in year) if len(year) == YEAR_MONTHS else None

    @cached_property
    def passenger_volume(self) -> Mapping[str, int]:
        """Passengers per airport (0 when unknown); only used to pick a default airport."""
        return {code: self.passengers_12m(code) or 0 for code in self.settings.coverage_airports}

    def city_codes(self) -> dict[str, frozenset[str]]:
        """Covered cities as lower-case words, with their airports, to catch a city named alone."""
        cities: dict[str, set[str]] = {}

        for airport in self.catalog.all():
            phrase = " ".join(search_words(airport.city))

            if phrase:
                cities.setdefault(phrase, set()).add(airport.iata)

        return {city: frozenset(codes) for city, codes in cities.items()}

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
