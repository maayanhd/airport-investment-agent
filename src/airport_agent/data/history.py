"""Per-airport monthly series shared by the data sources, and the checks they share."""

from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from typing import Generic, TypeVar

from pydantic import TypeAdapter, ValidationError

from airport_agent.data.provider import Fetched
from airport_agent.domain import Provenance

T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)


class History(Generic[T_co]):
    def __init__(
        self,
        series: dict[str, list[T_co]],
        provenance: Provenance,
        coverage: Iterable[str],
        *,
        min_months: int = 0,
    ) -> None:
        self._series = series
        self.provenance = provenance
        self.min_months = min_months
        self.missing_codes = sorted({c.upper() for c in coverage} - series.keys())
        self.short_codes = sorted(c for c, months in series.items() if len(months) < min_months)

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


def data_age_months(as_of: str, today: date) -> int | None:
    """Months between the data month ("YYYY-MM") and today, or None if it cannot be read."""
    try:
        year, month = (int(part) for part in as_of.split("-"))
    except ValueError:
        return None

    return (today.year - year) * 12 + today.month - month


def check_age(provenance: Provenance, *, max_age_months: int, today: date) -> Provenance:
    """Flag data whose month is older than the limit or unknown, and say why."""
    age = data_age_months(provenance.as_of, today)

    if age is not None and age <= max_age_months:
        return provenance

    warning = (
        "stale: data month unknown"
        if age is None
        else f"stale: data month {provenance.as_of} is {age} months old (limit {max_age_months})"
    )
    note = f"{provenance.note}; {warning}" if provenance.note else warning

    return provenance.model_copy(update={"note": note, "stale": True})


def build_history(
    fetched: Fetched[dict[str, list[T]]],
    coverage: Iterable[str],
    *,
    min_months: int,
    max_age_months: int,
    today: date,
) -> History[T]:
    provenance = check_age(fetched.provenance, max_age_months=max_age_months, today=today)

    return History(fetched.value, provenance, coverage, min_months=min_months)
