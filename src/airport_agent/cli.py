"""Command line to inspect and refresh the data layer without starting the agent."""

import argparse
from collections.abc import Sequence
from pathlib import Path

from airport_agent.config import DEFAULT_CONFIG_PATH, Settings, load_settings
from airport_agent.data.delays import load_delays, refresh_delays
from airport_agent.data.history import History
from airport_agent.data.http import HttpFetcher
from airport_agent.data.provider import DataProvider, DataUnavailableError
from airport_agent.data.snapshots import SnapshotStore
from airport_agent.data.traffic import TrafficHistory, load_traffic
from airport_agent.domain import DataMode

ROOT = DEFAULT_CONFIG_PATH.parent
SHOWN_MONTHS = 6


def _status(store: SnapshotStore) -> int:
    entries = store.entries()

    if not entries:
        print("no snapshots yet")

    invalid = 0

    for name, entry in sorted(entries.items()):
        fetched = f"{entry.fetched_at:%Y-%m-%d %H:%M} UTC"
        valid = store.load(name) is not None
        invalid += not valid
        health = "ok" if valid else "INVALID (file missing or bad checksum)"

        print(
            f"{name}: {entry.source} | data month {entry.as_of or 'n/a'} | fetched {fetched}"
            f" | {health}"
        )

    return 1 if invalid else 0


def _load(settings: Settings, store: SnapshotStore, *, persist: bool) -> TrafficHistory | None:
    try:
        return load_traffic(DataProvider(store, persist=persist), HttpFetcher(), settings)
    except DataUnavailableError as error:
        print(error)

        return None


def _refresh(which: str, settings: Settings, store: SnapshotStore) -> int:
    provider = DataProvider(store)
    fetcher = HttpFetcher()

    try:
        history: History[object] = (
            refresh_delays(provider, fetcher, settings)
            if which == "delays"
            else load_traffic(provider, fetcher, settings)
        )
    except DataUnavailableError as error:
        print(error)

        return 1

    return _report_refresh(history, settings)


def _report_refresh(history: History[object], settings: Settings) -> int:
    source = history.provenance

    if source.mode is not DataMode.LIVE:
        print(f"refresh failed, snapshot unchanged: {source.note}")

        return 1

    print(f"saved {source.source} | data month {source.as_of}")
    print(f"airports with data: {len(settings.coverage_airports) - len(history.missing_codes)}")

    if history.missing_codes:
        print(f"covered but not in the data: {', '.join(history.missing_codes)}")

    return 0


def _ratio(part: float, whole: float, *, scale: float = 1) -> str:
    return f"{scale * part / whole:>6.1f}" if whole else "   n/a"


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

    history = _load(settings, store, persist=False)

    if history is None:
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

    if source.note:
        print(f"note: {source.note}")

    print("month    departures  passengers      seats  load%  avg miles/flight")

    for row in rows:
        print(
            f"{row.month:%Y-%m}  {row.departures:>10}  {row.passengers:>10}  {row.seats:>9}"
            f"  {row.load_factor:>5.1f}  {row.avg_flight_distance_miles:>16.0f}"
        )

    _print_delays(code, settings, store)

    return 0


def main(
    argv: Sequence[str] | None = None, *, root: Path = ROOT, settings: Settings | None = None
) -> int:
    parser = argparse.ArgumentParser(prog="airport-agent")
    commands = (
        parser.add_subparsers(dest="command", required=True)
        .add_parser("data")
        .add_subparsers(dest="action", required=True)
    )
    commands.add_parser("status", help="list saved snapshots and check their checksums")
    refresh = commands.add_parser("refresh", help="fetch live data and save it as the snapshot")
    refresh.add_argument("source", nargs="?", choices=["traffic", "delays"], default="traffic")
    commands.add_parser("show", help="show recent monthly traffic (read-only)").add_argument(
        "airport"
    )
    args = parser.parse_args(argv)

    settings = settings or load_settings()
    store = SnapshotStore(root / settings.snapshot_dir)

    if args.action == "status":
        return _status(store)

    if args.action == "refresh":
        return _refresh(args.source, settings, store)

    return _show(args.airport, settings, store)


if __name__ == "__main__":
    raise SystemExit(main())
