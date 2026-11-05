import pytest

from airport_agent.display import passengers


@pytest.mark.parametrize(
    ("value", "shown"),
    [(5_234_567, "5.2 million"), (1_000_000, "1.0 million"), (612_345, "612,345"), (0, "0")],
)
def test_passengers_read_in_millions_from_a_million_up(value: int, shown: str) -> None:
    assert passengers(value) == shown
