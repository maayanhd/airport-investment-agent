"""Per-airport monthly series shared by the data sources, and the checks they share."""

from collections.abc import Iterable, Mapping, Sequence
from typing import Generic, TypeVar

from pydantic import TypeAdapter, ValidationError

from airport_agent.domain import Provenance

T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)


class History(Generic[T_co]):
    def __init__(
        self, series: dict[str, list[T_co]], provenance: Provenance, coverage: Iterable[str]
    ) -> None:
        self._series = series
        self.provenance = provenance
        self.missing_codes = sorted({c.upper() for c in coverage} - series.keys())

    def series(self, iata: str) -> list[T_co]:
        return self._series.get(iata.upper(), [])


def read_rows(adapter: TypeAdapter[list[T]], text: str, label: str) -> list[T]:
    """Name the first bad row and field, so the fallback note says what was wrong."""
    try:
        return adapter.validate_json(text)
    except ValidationError as error:
        first = error.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "response"

        raise ValueError(
            f"{label} {where}: {first['msg']} ({error.error_count()} problem(s))"
        ) from error


def require_enough(
    series: Mapping[str, Sequence[object]],
    covered: int,
    *,
    min_months: int,
    min_share: float,
    label: str,
) -> None:
    complete = sum(len(months) >= min_months for months in series.values())

    if complete < min_share * covered:
        raise ValueError(
            f"{label} response too thin: {complete} of {covered} covered airports have "
            f"{min_months}+ months (need {min_share:.0%})"
        )
