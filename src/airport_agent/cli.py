"""Command line to inspect and refresh the data layer without starting the agent."""

import argparse
from collections.abc import Sequence
from pathlib import Path

from airport_agent.config import DEFAULT_CONFIG_PATH, Settings, load_settings
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
        return load_traffic(DataProvider(store), HttpFetcher(), settings, persist=persist)
    except DataUnavailableError as error:
        print(error)

        return None


def _refresh(settings: Settings, store: SnapshotStore) -> int:
    history = _load(settings, store, persist=True)

    if history is None:
        return 1

    source = history.provenance

    if source.mode is not DataMode.LIVE:
        print(f"refresh failed, snapshot unchanged: {source.note}")

        return 1

    print(f"saved {source.source} | data month {source.as_of}")
    print(f"airports with data: {len(settings.coverage_airports) - len(history.missing_codes)}")

    if history.missing_codes:
        print(f"covered but not in the data: {', '.join(history.missing_codes)}")

    return 0


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
    commands.add_parser("refresh", help="fetch live data and save it as the snapshot")
    commands.add_parser("show", help="show recent monthly traffic (read-only)").add_argument(
        "airport"
    )
    args = parser.parse_args(argv)

    settings = settings or load_settings()
    store = SnapshotStore(root / settings.snapshot_dir)

    if args.action == "status":
        return _status(store)

    if args.action == "refresh":
        return _refresh(settings, store)

    return _show(args.airport, settings, store)


if __name__ == "__main__":
    raise SystemExit(main())
