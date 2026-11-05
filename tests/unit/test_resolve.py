import pytest
from hypothesis import given
from hypothesis import strategies as st

from airport_agent.config import Settings
from airport_agent.dataset import Dataset
from airport_agent.resolve import Match, Resolution, resolve_airport


def _resolve(dataset: Dataset, query: str) -> Resolution:
    return resolve_airport(
        query,
        dataset.catalog,
        dataset.settings.airport_aliases,
        max_candidates=dataset.settings.max_name_candidates,
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
    ("query", "code"),
    [
        ("Santa Ana", "SNA"),
        ("orange county", "SNA"),
        ("Los Angeles", "LAX"),
        ("new york", "JFK"),
        ("Boston", "BOS"),
    ],
)
def test_common_names_resolve_through_the_alias_table(
    dataset: Dataset, query: str, code: str
) -> None:
    assert _resolve(dataset, query).code == code


@pytest.mark.parametrize(
    ("query", "code"), [("anchorage", "ANC"), ("Boston Logan", "BOS"), ("Manchester", "MHT")]
)
def test_an_official_name_resolves_when_it_is_unique(
    dataset: Dataset, query: str, code: str
) -> None:
    found = _resolve(dataset, query)

    assert (found.status, found.code) == (Match.RESOLVED, code)
    assert found.alternatives == ()


def test_a_name_shared_by_two_airports_asks_instead_of_guessing(dataset: Dataset) -> None:
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
