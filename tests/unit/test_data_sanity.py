"""Properties that must hold for every covered airport, so a bad data refresh fails here."""

import pytest

from airport_agent.dataset import Dataset
from airport_agent.domain import search_words
from airport_agent.kpis import AirportKpis
from airport_agent.resolve import Match, resolve_airport
from airport_agent.tools.common import kpis_for, score_for

MAX_DELAY_RATE = 0.6
MAX_AVG_DISTANCE_MILES = 6000


@pytest.fixture(scope="module")
def all_kpis(dataset: Dataset) -> dict[str, AirportKpis]:
    return {code: kpis_for(dataset, code) for code in dataset.settings.coverage_airports}


def test_every_covered_airport_is_in_the_catalog_up_to_the_known_gaps(dataset: Dataset) -> None:
    missing = dataset.catalog.missing_codes
    allowed = (1 - dataset.settings.min_airport_share) * len(dataset.settings.coverage_airports)

    assert len(missing) <= allowed


def test_every_score_is_between_0_and_100_with_enough_confidence(
    dataset: Dataset, all_kpis: dict[str, AirportKpis]
) -> None:
    settings = dataset.settings

    for code, kpis in all_kpis.items():
        result = score_for(dataset, kpis)

        if result.score is None:
            assert result.confidence < settings.min_confidence, code
        else:
            assert 0 <= result.score <= 100, code
            assert settings.min_confidence <= result.confidence <= 1.0000001, code
            assert sum(c.points for c in result.components) == pytest.approx(result.score), code


def test_most_airports_get_a_score(dataset: Dataset, all_kpis: dict[str, AirportKpis]) -> None:
    scored = [c for c, k in all_kpis.items() if score_for(dataset, k).score is not None]

    assert len(scored) >= 0.9 * len(all_kpis)


def test_kpis_stay_in_plausible_ranges(all_kpis: dict[str, AirportKpis]) -> None:
    for code, kpis in all_kpis.items():
        if kpis.load_factor is not None:
            assert 0 < kpis.load_factor <= 100, code

        if kpis.delay_rate is not None:
            assert 0 <= kpis.delay_rate <= MAX_DELAY_RATE, code

        if kpis.cancel_rate is not None:
            assert 0 <= kpis.cancel_rate < 1, code

        if kpis.avg_distance_miles is not None:
            assert 0 < kpis.avg_distance_miles <= MAX_AVG_DISTANCE_MILES, code


def test_the_data_is_recent_enough_for_the_pinned_date(dataset: Dataset) -> None:
    assert not dataset.traffic.provenance.stale
    assert dataset.delays is not None
    assert not dataset.delays.provenance.stale


def test_every_covered_airport_resolves_to_itself_by_code(dataset: Dataset) -> None:
    settings = dataset.settings
    present = [c for c in settings.coverage_airports if c not in dataset.catalog.missing_codes]

    for code in present:
        found = resolve_airport(
            code,
            dataset.catalog,
            settings.airport_aliases,
            max_candidates=settings.max_name_candidates,
            volume=dataset.passenger_volume(),
        )

        assert (found.status, found.code) == (Match.RESOLVED, code), code


def test_every_airport_is_listed_under_its_own_city(dataset: Dataset) -> None:
    cities = dataset.city_codes()

    for airport in dataset.catalog.all():
        assert airport.iata in cities[" ".join(search_words(airport.city))], airport.iata

    assert cities["chicago"] == frozenset({"ORD", "MDW"})
