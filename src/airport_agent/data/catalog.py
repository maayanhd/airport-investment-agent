"""Airport identity and location from OurAirports, limited to the configured coverage."""

import csv
import io
from collections.abc import Iterable
from operator import attrgetter

from airport_agent.data.http import HttpFetcher
from airport_agent.data.provider import DataProvider
from airport_agent.domain import Airport, Provenance

OURAIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
SOURCE = "OurAirports"
SNAPSHOT_NAME = "ourairports_airports.csv"
SIZES = {"large_airport": "large", "medium_airport": "medium", "small_airport": "small"}
REQUIRED_COLUMNS = frozenset(
    {"iata_code", "iso_country", "type", "iso_region", "name", "latitude_deg", "longitude_deg"}
)


def _to_airport(row: dict[str, str], wanted: set[str]) -> Airport | None:
    iata = row["iata_code"].upper()

    if iata not in wanted or row["iso_country"] != "US" or row["type"] not in SIZES:
        return None

    return Airport(
        iata=iata,
        name=row["name"],
        state=row["iso_region"].removeprefix("US-"),
        latitude=float(row["latitude_deg"]),
        longitude=float(row["longitude_deg"]),
        size=SIZES[row["type"]],
    )


def parse_airports(text: str, coverage: Iterable[str]) -> dict[str, Airport]:
    """Raises ValueError, and only ValueError, on unusable content."""
    wanted = {code.upper() for code in coverage}
    reader = csv.DictReader(io.StringIO(text))

    if not set(reader.fieldnames or []) >= REQUIRED_COLUMNS:
        raise ValueError("not an OurAirports airports.csv (required columns missing)")

    airports: dict[str, Airport] = {}

    for row in reader:
        # DictReader marks short rows with None values and long rows with a None key.
        if None in row or None in row.values():
            raise ValueError(f"ragged row in airports.csv (line {reader.line_num})")

        airport = _to_airport(row, wanted)

        if airport is not None:
            airports[airport.iata] = airport

    if not airports:
        raise ValueError("no covered airport found in airports.csv")

    return airports


class OurAirportsCatalog:
    """Pure lookups over already-parsed airports; `load_catalog` does the I/O."""

    def __init__(
        self, airports: dict[str, Airport], provenance: Provenance, coverage: Iterable[str]
    ) -> None:
        self._airports = airports
        self.provenance = provenance

        self.missing_codes: list[str] = sorted({c.upper() for c in coverage} - airports.keys())

    def get(self, iata: str) -> Airport | None:
        return self._airports.get(iata.upper())

    def in_states(self, states: Iterable[str]) -> list[Airport]:
        wanted = {state.upper() for state in states}
        return sorted(
            (a for a in self._airports.values() if a.state in wanted), key=attrgetter("iata")
        )


def load_catalog(
    provider: DataProvider, fetcher: HttpFetcher, coverage: Iterable[str]
) -> OurAirportsCatalog:
    wanted = tuple(coverage)
    fetched = provider.fetch(
        SNAPSHOT_NAME,
        source=SOURCE,
        live=lambda: fetcher.get_text(OURAIRPORTS_URL),
        parse=lambda text: parse_airports(text, wanted),
    )

    return OurAirportsCatalog(fetched.value, fetched.provenance, wanted)
