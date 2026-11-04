"""Monthly delays and cancellations per origin airport, from the BTS On-Time bulk files."""

import csv
import io
import zipfile
import zlib
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date

from pydantic import TypeAdapter

from airport_agent.config import Settings
from airport_agent.data.history import History, build_history, read_rows, require_enough
from airport_agent.data.http import HttpFetcher, NotFoundError
from airport_agent.data.provider import DataProvider, Fetched
from airport_agent.domain import MonthlyDelays

URL = (
    "https://transtats.bts.gov/PREZIP/"
    "On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{year}_{month}.zip"
)
SOURCE = "BTS On-Time Reporting (reporting carriers only)"
SNAPSHOT_NAME = "bts_ontime_delays.json"
MAX_UNPUBLISHED_MONTHS = 4
REQUIRED_COLUMNS = frozenset({"FlightDate", "Origin", "Cancelled", "DepDel15", "DepDelayMinutes"})
_ROWS = TypeAdapter(list[MonthlyDelays])
DelayHistory = History[MonthlyDelays]


@dataclass
class _Totals:
    flights: int = 0
    cancelled: int = 0
    delayed_15: int = 0
    delay_minutes: float = 0.0


def aggregate_month(content: bytes, month: date, wanted: set[str]) -> list[MonthlyDelays]:
    """Raises ValueError, and only ValueError, if the file is not the expected month's data."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            name = next(n for n in archive.namelist() if n.endswith(".csv"))

            with archive.open(name) as raw:
                totals = _sum_rows(
                    io.TextIOWrapper(raw, encoding="utf-8", newline=""), month, wanted
                )
    except (zipfile.BadZipFile, StopIteration, zlib.error, EOFError, csv.Error) as error:
        raise ValueError(f"On-Time {month:%Y-%m}: not a readable zip with a CSV inside") from error

    return [
        MonthlyDelays(iata=code, month=month, **vars(total))
        for code, total in sorted(totals.items())
    ]


def _sum_rows(lines: io.TextIOWrapper, month: date, wanted: set[str]) -> dict[str, _Totals]:
    reader = csv.DictReader(lines)

    if not set(reader.fieldnames or []) >= REQUIRED_COLUMNS:
        raise ValueError("On-Time file is missing expected columns")

    totals: dict[str, _Totals] = defaultdict(_Totals)

    for row in reader:
        if row["Origin"] not in wanted:
            continue

        if None in row or None in row.values():
            raise ValueError(f"On-Time {month:%Y-%m}: ragged row at line {reader.line_num}")

        if not row["FlightDate"].startswith(f"{month:%Y-%m}"):
            raise ValueError(f"On-Time {month:%Y-%m}: found a flight dated {row['FlightDate']}")

        total = totals[row["Origin"]]
        total.flights += 1

        if _flag(row["Cancelled"]):  # some cancelled flights also carry a delay; ignore it
            total.cancelled += 1
        else:
            total.delayed_15 += _flag(row["DepDel15"], blank_ok=True)
            total.delay_minutes += float(row["DepDelayMinutes"] or 0)

    return totals


def _flag(value: str, *, blank_ok: bool = False) -> bool:
    """A 0/1 column in any numeric spelling. Anything else is a format change: fail loudly."""
    if blank_ok and not value.strip():
        return False

    try:
        number = float(value)
    except ValueError:
        number = -1.0

    if number not in (0.0, 1.0):
        raise ValueError(f"On-Time: unexpected flag value {value!r}")

    return number == 1.0


def _previous(month: date) -> date:
    return date(month.year - (month.month == 1), (month.month - 2) % 12 + 1, 1)


def _download(
    fetcher: HttpFetcher, wanted: set[str], today: date, months: int
) -> list[MonthlyDelays]:
    """The newest months may not be published yet, so skip those that are not there."""
    rows: list[MonthlyDelays] = []
    month = _previous(today.replace(day=1))
    done = skipped = 0

    while done < months:
        try:
            content = fetcher.get_bytes(URL.format(year=month.year, month=month.month))
        except NotFoundError:
            if done or skipped >= MAX_UNPUBLISHED_MONTHS:
                raise

            skipped += 1
        else:
            rows += aggregate_month(content, month, wanted)
            done += 1

        month = _previous(month)

    return rows


def parse_delays(
    text: str, coverage: Iterable[str], *, min_months: int, min_airport_share: float
) -> dict[str, list[MonthlyDelays]]:
    """Raises ValueError, and only ValueError, on unusable or too-thin content."""
    wanted = {code.upper() for code in coverage}
    series: dict[str, list[MonthlyDelays]] = defaultdict(list)

    for row in read_rows(_ROWS, text, "On-Time"):
        if row.iata in wanted:
            series[row.iata].append(row)

    for months in series.values():
        months.sort(key=lambda m: m.month)

    require_enough(
        series, len(wanted), min_months=min_months, min_share=min_airport_share, label="On-Time"
    )

    return dict(series)


def _latest_label(series: dict[str, list[MonthlyDelays]]) -> str:
    return max(m.month for months in series.values() for m in months).strftime("%Y-%m")


def _parser(
    settings: Settings, wanted: tuple[str, ...]
) -> Callable[[str], dict[str, list[MonthlyDelays]]]:
    return lambda text: parse_delays(
        text,
        wanted,
        min_months=settings.delays_min_months,
        min_airport_share=settings.min_airport_share,
    )


def refresh_delays(
    provider: DataProvider, fetcher: HttpFetcher, settings: Settings, *, today: date | None = None
) -> DelayHistory:
    """Downloads one 31 MB file per month, so this is for `data refresh`, not for requests."""
    wanted = tuple(settings.coverage_airports)
    today = today or date.today()
    fetched = provider.fetch(
        SNAPSHOT_NAME,
        source=SOURCE,
        live=lambda: _ROWS.dump_json(
            _download(fetcher, set(wanted), today, settings.delays_months)
        ).decode(),
        parse=_parser(settings, wanted),
        as_of=_latest_label,
    )

    return _history(fetched, wanted, settings, today)


def load_delays(
    provider: DataProvider, settings: Settings, *, today: date | None = None
) -> DelayHistory:
    wanted = tuple(settings.coverage_airports)
    fetched = provider.read_snapshot(SNAPSHOT_NAME, source=SOURCE, parse=_parser(settings, wanted))

    return _history(fetched, wanted, settings, today or date.today())


def _history(
    fetched: Fetched[dict[str, list[MonthlyDelays]]],
    wanted: tuple[str, ...],
    settings: Settings,
    today: date,
) -> DelayHistory:
    return build_history(
        fetched,
        wanted,
        min_months=settings.delays_min_months,
        max_age_months=settings.max_data_age_months,
        today=today,
    )
