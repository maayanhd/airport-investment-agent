"""Command line to inspect the data layer without starting the agent."""

import argparse
from collections.abc import Sequence
from pathlib import Path

from airport_agent.config import DEFAULT_CONFIG_PATH, Settings, load_settings
from airport_agent.data.http import HttpFetcher
from airport_agent.data.provider import DataProvider, DataUnavailableError
from airport_agent.data.snapshots import SnapshotStore
from airport_agent.data.traffic import load_traffic

ROOT = DEFAULT_CONFIG_PATH.parent
SHOWN_MONTHS = 6


def _status(store: SnapshotStore) -> int:
    entries = store.entries()

    if not entries:
        print("no snapshots yet")

    for name, entry in sorted(entries.items()):
        fetched = f"{entry.fetched_at:%Y-%m-%d %H:%M} UTC"
        print(f"{name}: {entry.source} | data month {entry.as_of or 'n/a'} | fetched {fetched}")

    return 0


def _show(code: str, settings: Settings, store: SnapshotStore) -> int:
    code = code.upper()

    if code not in settings.coverage_airports:
        print(f"{code} is not in the coverage list")
        return 2

    try:
        history = load_traffic(
            DataProvider(store),
            HttpFetcher(),
            settings.coverage_airports,
            months=settings.traffic_history_months,
        )
    except DataUnavailableError as error:
        print(error)
        return 1

    source = history.provenance
    print(f"{code} | {source.source} | {source.mode.value} | data month {source.as_of}")

    if source.note:
        print(f"note: {source.note}")

    rows = history.series(code)[-SHOWN_MONTHS:]

    if not rows:
        print(f"{code} has no rows in the data")
        return 3

    print("month    departures  passengers      seats  load%  avg miles/flight")

    for row in rows:
        print(
            f"{row.month:%Y-%m}  {row.departures:>10}  {row.passengers:>10}  {row.seats:>9}"
            f"  {row.load_factor:>5.1f}  {row.avg_flight_distance_miles:>16.0f}"
        )

    return 0


def main(argv: Sequence[str] | None = None, *, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(prog="airport-agent")
    commands = (
        parser.add_subparsers(dest="command", required=True)
        .add_parser("data")
        .add_subparsers(dest="action", required=True)
    )
    commands.add_parser("status", help="list saved snapshots")
    commands.add_parser("show", help="show recent monthly traffic").add_argument("airport")
    args = parser.parse_args(argv)

    settings = load_settings()
    store = SnapshotStore(root / settings.snapshot_dir)

    return _status(store) if args.action == "status" else _show(args.airport, settings, store)


if __name__ == "__main__":
    raise SystemExit(main())
