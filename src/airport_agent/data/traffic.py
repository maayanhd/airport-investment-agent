"""Monthly passengers and capacity per origin airport, from BTS T-100 (Socrata API)."""

import re
from collections import defaultdict
from collections.abc import Iterable
from datetime import date

from pydantic import BaseModel, TypeAdapter

from airport_agent.data.http import HttpFetcher
from airport_agent.data.provider import DataProvider
from airport_agent.domain import MonthlyTraffic, Provenance

BTS_URL = "https://data.bts.gov/resource/r495-tyji.json"
SOURCE = "BTS T-100 (summary by origin airport)"
SNAPSHOT_NAME = "bts_t100_traffic.json"
MAX_ROWS = 50_000


class _Row(BaseModel):
    origin_airport_code: str
    reporting_month: date
    total_departures: int
    total_passengers: int
    total_seats: int
    total_load_factor: float
    total_distance_flight_sm: float


COLUMNS = ",".join(_Row.model_fields)
_ROWS = TypeAdapter(list[_Row])


def _to_traffic(row: _Row) -> MonthlyTraffic:
    return MonthlyTraffic(
        iata=row.origin_airport_code,
        month=row.reporting_month,
        departures=row.total_departures,
        passengers=row.total_passengers,
        seats=row.total_seats,
        load_factor=row.total_load_factor,
        avg_flight_distance_miles=row.total_distance_flight_sm,
    )


def parse_traffic(text: str, coverage: Iterable[str]) -> dict[str, list[MonthlyTraffic]]:
    """Raises ValueError, and only ValueError, on unusable content."""
    wanted = {code.upper() for code in coverage}
    series: dict[str, list[MonthlyTraffic]] = defaultdict(list)

    for row in _ROWS.validate_json(text):
        if row.origin_airport_code in wanted:
            series[row.origin_airport_code].append(_to_traffic(row))

    if not series:
        raise ValueError("no covered airport found in BTS response")

    for code, months in series.items():
        months.sort(key=lambda m: m.month)

        if len({m.month for m in months}) != len(months):
            raise ValueError(f"duplicate months for {code} in BTS response")

    return dict(series)


class TrafficHistory:
    def __init__(
        self,
        series: dict[str, list[MonthlyTraffic]],
        provenance: Provenance,
        coverage: Iterable[str],
    ) -> None:
        self._series = series
        self.provenance = provenance
        self.missing_codes = sorted({c.upper() for c in coverage} - series.keys())
        self.latest_month = max(m.month for months in series.values() for m in months)

    def series(self, iata: str) -> list[MonthlyTraffic]:
        return self._series.get(iata.upper(), [])


def _months_back(today: date, months: int) -> date:
    index = today.year * 12 + today.month - 1 - months

    return date(index // 12, index % 12 + 1, 1)


def _query(codes: tuple[str, ...], since: date) -> dict[str, str]:
    listed = ",".join(f"'{code}'" for code in codes)

    return {
        "$select": COLUMNS,
        "$where": f"origin_airport_code in ({listed}) AND reporting_month >= '{since}T00:00:00'",
        "$order": "origin_airport_code, reporting_month",
        "$limit": str(MAX_ROWS),
    }


def _latest_label(series: dict[str, list[MonthlyTraffic]]) -> str:
    return max(m.month for months in series.values() for m in months).strftime("%Y-%m")


def load_traffic(
    provider: DataProvider,
    fetcher: HttpFetcher,
    coverage: Iterable[str],
    *,
    months: int,
    today: date | None = None,
) -> TrafficHistory:
    wanted = tuple(code.upper() for code in coverage)

    if not all(re.fullmatch(r"[A-Z]{3}", code) for code in wanted):
        raise ValueError("coverage must be 3-letter IATA codes")

    since = _months_back(today or date.today(), months)
    fetched = provider.fetch(
        SNAPSHOT_NAME,
        source=SOURCE,
        live=lambda: fetcher.get_text(BTS_URL, params=_query(wanted, since)),
        parse=lambda text: parse_traffic(text, wanted),
        as_of=_latest_label,
    )

    return TrafficHistory(fetched.value, fetched.provenance, wanted)
