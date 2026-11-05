import shutil
from pathlib import Path

import pytest

from airport_agent.config import Settings
from airport_agent.data.http import HttpFetcher
from airport_agent.data.provider import DataUnavailableError
from airport_agent.data.snapshots import SnapshotStore
from airport_agent.dataset import load_dataset


def test_the_committed_snapshots_load_into_one_consistent_dataset(
    settings: Settings, repo: Path, offline_fetcher: HttpFetcher
) -> None:
    data = load_dataset(settings, SnapshotStore(repo / settings.snapshot_dir), offline_fetcher)

    assert data.warnings == ()
    assert data.delays is not None
    assert len(data.sources()) == 3
    assert data.catalog.get("BOS") is not None
    assert data.traffic.series("BOS")


def test_missing_delay_data_is_a_warning_not_a_failure(
    settings: Settings, repo: Path, tmp_path: Path, offline_fetcher: HttpFetcher
) -> None:
    source = repo / settings.snapshot_dir

    for name in ("ourairports_airports.json", "bts_t100_traffic.json", "manifest.json"):
        shutil.copy(source / name, tmp_path / name)

    data = load_dataset(settings, SnapshotStore(tmp_path), offline_fetcher)

    assert data.delays is None
    assert "delay_pressure is missing for every airport" in data.warnings[0]
    assert len(data.sources()) == 2


def test_no_catalog_snapshot_is_a_failure(
    settings: Settings, tmp_path: Path, offline_fetcher: HttpFetcher
) -> None:
    with pytest.raises(DataUnavailableError, match="no valid snapshot"):
        load_dataset(settings, SnapshotStore(tmp_path), offline_fetcher)
