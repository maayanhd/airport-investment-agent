from collections.abc import Mapping

import pytest
from hypothesis import given
from hypothesis import strategies as st

from airport_agent.config import Settings
from airport_agent.dataset import Dataset
from airport_agent.resolve import Match, Resolution, resolve_airport


def _resolve(dataset: Dataset, query: str, volume: Mapping[str, int] | None = None) -> Resolution:
    return resolve_airport(
        query,
        dataset.catalog,
        dataset.settings.airport_aliases,
        max_candidates=dataset.settings.max_name_candidates,
        volume=dataset.passenger_volume() if volume is None else volume,
    )


@pytest.mark.parametrize("query", ["BOS", "bos", "  Bos "])
def test_a_covered_code_resolves_without_a_note(dataset: Dataset, query: str) -> None:
    found = _resolve(dataset, query)

    assert (found.status, found.code, found.note) == (Match.RESOLVED, "BOS", "")


def test_la_is_read_as_lax_and_the_other_readings_are_offered(dataset: Dataset) -> None:
    found = _resolve(dataset, "LA")

    assert (found.status, found.code) == (Match.RESOLVED, "LAX")
    assert found.alternatives == ("BUR", "LGB", "ONT", "SNA")
    assert '"LA" read as LAX' in found.note
    assert "other readings: BUR, LGB, ONT, SNA" in found.note


@pytest.mark.parametrize(
    ("query", "code", "others"),
    [
        ("new york", "JFK", ("LGA", "EWR")),
        ("DC", "DCA", ("IAD", "BWI")),
        ("bay area", "SFO", ("OAK", "SJC")),
        ("Dallas", "DFW", ("DAL",)),
        ("orange county", "SNA", ()),
    ],
)
def test_metro_names_come_from_the_alias_table(
    dataset: Dataset, query: str, code: str, others: tuple[str, ...]
) -> None:
    found = _resolve(dataset, query)

    assert (found.code, found.alternatives) == (code, others)


@pytest.mark.parametrize(
    ("query", "code"),
    [
        ("Santa Ana", "SNA"),
        ("burbank", "BUR"),
        ("Anchorage", "ANC"),
        ("Boston", "BOS"),
        ("Manchester", "MHT"),
        ("Providence", "PVD"),
        ("Boston Logan", "BOS"),
    ],
)
def test_a_city_or_name_in_the_data_needs_no_alias(dataset: Dataset, query: str, code: str) -> None:
    found = _resolve(dataset, query)

    assert (found.status, found.code, found.alternatives) == (Match.RESOLVED, code, ())
    assert query not in dataset.settings.airport_aliases


@pytest.mark.parametrize(
    ("query", "code", "other"), [("Chicago", "ORD", "MDW"), ("Houston", "IAH", "HOU")]
)
def test_the_busiest_airport_is_the_default_inside_one_city(
    dataset: Dataset, query: str, code: str, other: str
) -> None:
    found = _resolve(dataset, query)

    assert (found.status, found.code, found.alternatives) == (Match.RESOLVED, code, (other,))
    assert f"other readings: {other}" in found.note


def test_the_default_follows_the_passenger_numbers_not_a_hand_picked_list(
    dataset: Dataset,
) -> None:
    found = _resolve(dataset, "Chicago", volume={"MDW": 90, "ORD": 10})

    assert (found.code, found.alternatives) == ("MDW", ("ORD",))


def test_equal_volumes_fall_back_to_the_code_order(dataset: Dataset) -> None:
    assert _resolve(dataset, "Chicago", volume={}).code == "MDW"


def test_the_same_city_name_in_two_states_asks_instead_of_guessing(dataset: Dataset) -> None:
    found = _resolve(dataset, "Portland")

    assert found.status is Match.AMBIGUOUS
    assert found.code is None
    assert found.alternatives == ("PDX", "PWM")


def test_a_code_outside_coverage_is_not_covered(dataset: Dataset) -> None:
    found = _resolve(dataset, "XYZ")

    assert (found.status, found.code) == (Match.NOT_COVERED, None)
    assert "XYZ" in found.note


@pytest.mark.parametrize("query", ["Atlantis", "", "   ", "!!!"])
def test_unknown_or_empty_input_is_unknown(dataset: Dataset, query: str) -> None:
    assert _resolve(dataset, query).status is Match.UNKNOWN


def test_every_configured_alias_resolves_to_its_first_code(
    dataset: Dataset, settings: Settings
) -> None:
    for alias, codes in settings.airport_aliases.items():
        found = _resolve(dataset, alias)

        assert (found.code, found.alternatives) == (codes[0], tuple(codes[1:]))


def test_every_covered_airport_can_be_reached_by_its_own_city(dataset: Dataset) -> None:
    for airport in dataset.catalog.all():
        found = _resolve(dataset, airport.city)

        assert airport.iata in {found.code, *found.alternatives}, (airport.iata, airport.city)


@given(st.text())
def test_any_text_gives_a_resolution_and_never_an_error(dataset: Dataset, query: str) -> None:
    found = _resolve(dataset, query)

    assert (found.code is not None) == (found.status is Match.RESOLVED)


@pytest.mark.parametrize("query", ["international", "airport"])
def test_a_vague_name_asks_for_something_clearer(dataset: Dataset, query: str) -> None:
    found = _resolve(dataset, query)

    assert (found.status, found.code, found.alternatives) == (Match.AMBIGUOUS, None, ())
    assert "give a city, a state or an airport code" in found.note


@pytest.mark.parametrize("query", ["L.A.", "l a", "Los-Angeles", "N.Y.C."])
def test_punctuation_and_spacing_do_not_hide_an_alias(dataset: Dataset, query: str) -> None:
    assert _resolve(dataset, query).status is Match.RESOLVED
