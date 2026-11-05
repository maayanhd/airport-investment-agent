from datetime import date
from pathlib import Path

import httpx
import pytest

from airport_agent.config import Settings, load_settings
from airport_agent.data.http import HttpFetcher
from airport_agent.data.snapshots import SnapshotStore
from airport_agent.dataset import Dataset, load_dataset

REPO = Path(__file__).parents[1]
TODAY = date(2026, 11, 5)  # pinned, so staleness does not change what the tests see


def _offline_fetcher() -> HttpFetcher:
    """Every request fails, so loaders fall back to the committed snapshots."""
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503)))

    return HttpFetcher(client, retries=0, sleep=lambda _: None)


@pytest.fixture(scope="session")
def settings() -> Settings:
    return load_settings()


@pytest.fixture(scope="session")
def dataset(settings: Settings) -> Dataset:
    """The real committed snapshots, read-only: what the agent serves from."""
    store = SnapshotStore(REPO / settings.snapshot_dir)

    return load_dataset(settings, store, _offline_fetcher(), today=TODAY)


@pytest.fixture
def repo() -> Path:
    return REPO


@pytest.fixture
def offline_fetcher() -> HttpFetcher:
    return _offline_fetcher()
