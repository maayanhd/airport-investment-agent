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
    """Raises ValueError, and only ValueError, for any unusable content: wrong file, missing
    columns, truncated or malformed rows, or no covered airport. Callers rely on this to fall
    back to a snapshot."""
    wanted = {code.upper() for code in coverage}
    reader = csv.DictReader(io.StringIO(text))
    if not set(reader.fieldnames or []) >= REQUIRED_COLUMNS:
        raise ValueError("not an OurAirports airports.csv (required columns missing)")
    airports: dict[str, Airport] = {}
    try:
        for row in reader:
            airport = _to_airport(row, wanted)
            if airport is not None:
                airports[airport.iata] = airport
    except (TypeError, AttributeError, KeyError) as error:
        raise ValueError(f"malformed or truncated row in airports.csv: {error!r}") from error
    if not airports:
        raise ValueError("no covered airport found in airports.csv")
    return airports


class OurAirportsCatalog:
    def __init__(self, provider: DataProvider, fetcher: HttpFetcher, coverage: Iterable[str]):
        wanted = tuple(coverage)
        fetched = provider.fetch_text(
            SNAPSHOT_NAME,
            source=SOURCE,
            live=lambda: fetcher.get_text(OURAIRPORTS_URL),
            validate=lambda text: parse_airports(text, wanted),
        )
        self._airports = parse_airports(fetched.text, wanted)
        self.provenance: Provenance = fetched.provenance
        # Covered codes the data does not contain: callers must say "not in the data",
        # not "not covered" (they cannot be told apart from `get() is None` alone).
        self.missing_codes: list[str] = sorted({c.upper() for c in wanted} - self._airports.keys())

    def get(self, iata: str) -> Airport | None:
        return self._airports.get(iata.upper())

    def in_states(self, states: Iterable[str]) -> list[Airport]:
        wanted = {state.upper() for state in states}
        return sorted((a for a in self._airports.values() if a.state in wanted), key=_by_iata)

    def all(self) -> list[Airport]:
        return sorted(self._airports.values(), key=_by_iata)


def _by_iata(airport: Airport) -> str:
    return airport.iata
