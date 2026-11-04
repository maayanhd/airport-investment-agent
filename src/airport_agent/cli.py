"""Command line to inspect and refresh the data layer without starting the agent."""

import argparse
from collections.abc import Callable, Sequence
from datetime import date
from enum import StrEnum
from pathlib import Path

from airport_agent.config import DEFAULT_CONFIG_PATH, Settings, load_settings
from airport_agent.data.delays import load_delays, refresh_delays
from airport_agent.data.history import History, data_age_months
from airport_agent.data.http import HttpFetcher
from airport_agent.data.provider import DataProvider, DataUnavailableError
from airport_agent.data.snapshots import SnapshotStore
from airport_agent.data.traffic import load_traffic
from airport_agent.domain import DataMode
from airport_agent.kpis import AirportKpis, compute_kpis
from airport_agent.scoring import AirportScore, score_airport

ROOT = DEFAULT_CONFIG_PATH.parent
SHOWN_MONTHS = 6


def _status(store: SnapshotStore, settings: Settings) -> int:
    entries = store.entries()

    if not entries:
        print("no snapshots yet")

    invalid = 0

    for name, entry in sorted(entries.items()):
        fetched = f"{entry.fetched_at:%Y-%m-%d %H:%M} UTC"
        valid = store.load(name) is not None
        invalid += not valid
        health = "ok" if valid else "INVALID (file missing or bad checksum)"
        age = data_age_months(entry.as_of, date.today())

        if age is None or age > settings.max_data_age_months:
            health += " | STALE"

        print(
            f"{name}: {entry.source} | data month {entry.as_of or 'n/a'} | fetched {fetched}"
            f" | {health}"
        )

    return 1 if invalid else 0


class RefreshSource(StrEnum):
    TRAFFIC = "traffic"
    DELAYS = "delays"


REFRESHERS: dict[
    RefreshSource, Callable[[DataProvider, HttpFetcher, Settings], History[object]]
] = {
    RefreshSource.TRAFFIC: load_traffic,
    RefreshSource.DELAYS: refresh_delays,
}


def _refresh(which: RefreshSource, settings: Settings, store: SnapshotStore) -> int:
    try:
        history = REFRESHERS[which](DataProvider(store), HttpFetcher(), settings)
    except DataUnavailableError as error:
        print(error)

        return 1

    source = history.provenance

    if source.save_failed:
        print(f"downloaded but could not write the snapshot: {source.note}")

        return 1

    if source.mode is not DataMode.LIVE:
        print(f"refresh failed, snapshot unchanged: {source.note}")

        return 1

    print(f"saved {source.source} | data month {source.as_of}")
    print(f"airports with data: {len(settings.coverage_airports) - len(history.missing_codes)}")

    if history.missing_codes:
        print(f"covered but not in the data: {', '.join(history.missing_codes)}")

    if source.stale:
        print(f"warning: {source.note}")

    return 0


def _ratio(part: float, whole: float, *, scale: float = 1) -> str:
    return f"{scale * part / whole:>6.1f}" if whole else "   n/a"


def _print_caveats(history: History[object], code: str) -> None:
    if history.provenance.note:
        print(f"note: {history.provenance.note}")

    if code in history.short_codes:
        months = len(history.series(code))
        print(f"note: only {months} month(s) for {code} (need {history.min_months}), so do not")
        print("      compare years or treat it as a full-year average")


def _print_traffic(code: str, settings: Settings, store: SnapshotStore) -> int:
    try:
        history = load_traffic(DataProvider(store, persist=False), HttpFetcher(), settings)
    except DataUnavailableError as error:
        print(error)

        return 1

    source = history.provenance
    rows = history.series(code)[-SHOWN_MONTHS:]

    if not rows:
        print(f"{code} has no rows in the data (dataset through {source.as_of})")

        return 3

    print(
        f"{code} | {source.source} | {source.mode.value} | {code} data through "
        f"{rows[-1].month:%Y-%m} (dataset through {source.as_of})"
    )
    _print_caveats(history, code)
    print("month    departures  passengers      seats  load%  avg miles/flight")

    for row in rows:
        print(
            f"{row.month:%Y-%m}  {row.departures:>10}  {row.passengers:>10}  {row.seats:>9}"
            f"  {row.load_factor:>5.1f}  {row.avg_flight_distance_miles:>16.0f}"
        )

    return 0


def _print_delays(code: str, settings: Settings, store: SnapshotStore) -> None:
    try:
        delays = load_delays(DataProvider(store, persist=False), settings)
    except DataUnavailableError:
        print("delays: no snapshot yet, run `airport-agent data refresh delays`")

        return

    rows = delays.series(code)[-SHOWN_MONTHS:]

    if not rows:
        print(f"delays: no On-Time rows for {code} (no reporting carrier serves it)")

        return

    source = delays.provenance
    print(f"delays | {source.source} | data through {rows[-1].month:%Y-%m} | {source.mode.value}")
    _print_caveats(delays, code)
    print("month    flights  cancelled%  late15%  avg delay min")

    for row in rows:
        operated = row.flights - row.cancelled
        cancelled = _ratio(row.cancelled, row.flights, scale=100)
        late = _ratio(row.delayed_15, operated, scale=100)
        average = _ratio(row.delay_minutes, operated)

        print(f"{row.month:%Y-%m}  {row.flights:>7}  {cancelled:>10}  {late:>7}  {average:>13}")


def _show(code: str, settings: Settings, store: SnapshotStore) -> int:
    code = code.upper()

    if code not in settings.coverage_airports:
        print(f"{code} is not in the coverage list")

        return 2

    status = _print_traffic(code, settings, store)
    _print_delays(code, settings, store)

    return status


SHOWN_VALUE = {
    "passenger_growth": lambda v: f"{v:+.1%}",
    "load_factor": lambda v: f"{v:.1f}%",
    "delay_pressure": lambda v: f"{v:.1%} late 15+ min",
    "capacity_gap": lambda v: f"{100 * v:+.1f} pts",
}


def _print_score(result: AirportScore, through: str) -> None:
    shown = "no score" if result.score is None else f"score {result.score:.1f} / 100"
    print(f"{result.iata} | {shown} | confidence {result.confidence:.0%} | {through}")

    if result.reason:
        print(f"  no score: {result.reason}")

    for c in result.components:
        value = SHOWN_VALUE[c.name](c.value)
        print(
            f"  {c.name:<17} {value:<19} scaled {c.scaled:.2f} x {c.weight:.2f} = {c.points:5.1f}"
        )

    if result.missing:
        print(f"  missing (not enough data): {', '.join(result.missing)}")


def _through(kpis: AirportKpis) -> str:
    parts = [
        f"{name} through {month:%Y-%m}"
        for name, month in (("traffic", kpis.traffic_through), ("delays", kpis.delays_through))
        if month
    ]

    return ", ".join(parts)


def _score(codes: Sequence[str], settings: Settings, store: SnapshotStore) -> int:
    wanted = [code.upper() for code in codes]
    unknown = [code for code in wanted if code not in settings.coverage_airports]

    if unknown:
        print(f"not in the coverage list: {', '.join(unknown)}")

        return 2

    try:
        traffic = load_traffic(DataProvider(store, persist=False), HttpFetcher(), settings)
    except DataUnavailableError as error:
        print(error)

        return 1

    try:
        delays = load_delays(DataProvider(store, persist=False), settings)
    except DataUnavailableError:
        delays = None

    for code in wanted:
        kpis = compute_kpis(
            code,
            traffic.series(code),
            delays.series(code) if delays else [],
            min_delay_months=settings.delays_min_months,
        )
        result = score_airport(
            kpis,
            weights=settings.score_weights,
            bounds=settings.score_bounds,
            min_confidence=settings.min_confidence,
        )
        _print_score(result, _through(kpis))

    print("capacity_gap = passenger growth minus seat growth; delay_pressure is a congestion proxy")
    print("scaled = position inside the fixed range in config.yaml (score_bounds), from 0 to 1")

    return 0


def main(
    argv: Sequence[str] | None = None, *, root: Path = ROOT, settings: Settings | None = None
) -> int:
    parser = argparse.ArgumentParser(prog="airport-agent")
    top = parser.add_subparsers(dest="command", required=True)
    commands = top.add_parser("data").add_subparsers(dest="action", required=True)
    top.add_parser("score", help="expansion score and its components").add_argument(
        "airports", nargs="+"
    )
    commands.add_parser("status", help="list saved snapshots and check their checksums")
    refresh = commands.add_parser("refresh", help="fetch live data and save it as the snapshot")
    refresh.add_argument(
        "source", nargs="?", type=RefreshSource, choices=list(RefreshSource), default="traffic"
    )
    commands.add_parser("show", help="show recent monthly traffic (read-only)").add_argument(
        "airport"
    )
    args = parser.parse_args(argv)

    settings = settings or load_settings()
    store = SnapshotStore(root / settings.snapshot_dir)

    if args.command == "score":
        return _score(args.airports, settings, store)

    if args.action == "status":
        return _status(store, settings)

    if args.action == "refresh":
        return _refresh(args.source, settings, store)

    return _show(args.airport, settings, store)


if __name__ == "__main__":
    raise SystemExit(main())
