import pytest
from pydantic import ValidationError

from airport_agent.domain import Airport


def make(**overrides: object) -> Airport:
    base: dict[str, object] = {
        "iata": "BOS",
        "name": "Logan",
        "state": "MA",
        "latitude": 42.36,
        "longitude": -71.0,
        "size": "large",
    }
    return Airport.model_validate({**base, **overrides})


def test_valid_airport() -> None:
    assert make().size == "large"


@pytest.mark.parametrize(
    "bad", [{"size": "huge"}, {"state": ""}, {"state": "Mass"}, {"iata": "bos"}, {"iata": "B0S"}]
)
def test_invalid_airport_fields_rejected(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        make(**bad)
