"""Monthly passengers and capacity per origin airport, from BTS T-100 (Socrata API)."""

from collections import defaultdict
from collections.abc import Iterable
from datetime import date

from pydantic import BaseModel, TypeAdapter

from airport_agent.config import Settings
from airport_agent.data.history import History, build_history, read_rows, require_enough
from airport_agent.data.http import HttpFetcher
from airport_agent.data.provider import DataProvider
from airport_agent.domain import MonthlyTraffic

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


def _group_by_airport(rows: list[_Row], wanted: set[str]) -> dict[str, list[MonthlyTraffic]]:
    series: dict[str, list[MonthlyTraffic]] = defaultdict(list)

    for row in rows:
        if row.origin_airport_code in wanted:
            series[row.origin_airport_code].append(_to_traffic(row))

    for code, months in series.items():
        months.sort(key=lambda m: m.month)

        if len({m.month for m in months}) != len(months):
            raise ValueError(f"duplicate months for {code} in BTS response")

    return dict(series)


def parse_traffic(
    text: str, coverage: Iterable[str], *, min_months: int, min_airport_share: float
) -> dict[str, list[MonthlyTraffic]]:
    """Raises ValueError, and only ValueError, on unusable, truncated or too-thin content."""
    wanted = {code.upper() for code in coverage}
    rows = read_rows(_ROWS, text, "BTS")

    if len(rows) >= MAX_ROWS:
        raise ValueError(f"BTS response reached the {MAX_ROWS} row limit and may be truncated")

    series = _group_by_airport(rows, wanted)
    require_enough(
        series, len(wanted), min_months=min_months, min_share=min_airport_share, label="BTS"
    )

    return series


TrafficHistory = History[MonthlyTraffic]


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
    settings: Settings,
    *,
    today: date | None = None,
) -> TrafficHistory:
    wanted = tuple(code.upper() for code in settings.coverage_airports)

    today = today or date.today()
    since = _months_back(today, settings.traffic_history_months)
    fetched = provider.fetch(
        SNAPSHOT_NAME,
        source=SOURCE,
        live=lambda: fetcher.get_text(BTS_URL, params=_query(wanted, since)),
        parse=lambda text: parse_traffic(
            text,
            wanted,
            min_months=settings.traffic_min_months,
            min_airport_share=settings.min_airport_share,
        ),
        as_of=_latest_label,
    )

    return build_history(
        fetched,
        wanted,
        min_months=settings.traffic_min_months,
        max_age_months=settings.max_data_age_months,
        today=today,
    )
