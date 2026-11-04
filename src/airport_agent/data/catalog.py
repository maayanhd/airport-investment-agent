"""Airport identity and location from OurAirports, limited to the configured coverage."""

import csv
import io
from collections.abc import Iterable

from airport_agent.data.http import HttpFetcher
from airport_agent.data.provider import DataProvider
from airport_agent.domain import Airport, Provenance

OURAIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
SOURCE = "OurAirports"
SNAPSHOT_NAME = "ourairports_airports.csv"
SIZES = {"large_airport": "large", "medium_airport": "medium", "small_airport": "small"}


def parse_airports(text: str, coverage: Iterable[str]) -> dict[str, Airport]:
    wanted = {code.upper() for code in coverage}
    airports: dict[str, Airport] = {}
    for row in csv.DictReader(io.StringIO(text)):
        iata = row["iata_code"].upper()
        if iata not in wanted or row["iso_country"] != "US" or row["type"] not in SIZES:
            continue
        airports[iata] = Airport(
            iata=iata,
            name=row["name"],
            state=row["iso_region"].removeprefix("US-"),
            latitude=float(row["latitude_deg"]),
            longitude=float(row["longitude_deg"]),
            size=SIZES[row["type"]],
        )
    return airports


class OurAirportsCatalog:
    def __init__(self, provider: DataProvider, fetcher: HttpFetcher, coverage: Iterable[str]):
        fetched = provider.fetch_text(
            SNAPSHOT_NAME, source=SOURCE, live=lambda: fetcher.get_text(OURAIRPORTS_URL)
        )
        self._airports = parse_airports(fetched.text, coverage)
        self.provenance: Provenance = fetched.provenance

    def get(self, iata: str) -> Airport | None:
        return self._airports.get(iata.upper())

    def in_states(self, states: Iterable[str]) -> list[Airport]:
        wanted = {state.upper() for state in states}
        return sorted((a for a in self._airports.values() if a.state in wanted), key=_by_iata)

    def all(self) -> list[Airport]:
        return sorted(self._airports.values(), key=_by_iata)


def _by_iata(airport: Airport) -> str:
    return airport.iata
