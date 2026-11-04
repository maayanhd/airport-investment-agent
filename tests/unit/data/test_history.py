from datetime import UTC, date, datetime

import pytest

from airport_agent.data.history import History, check_age
from airport_agent.domain import DataMode, Provenance


def _provenance(as_of: str, note: str = "") -> Provenance:
    return Provenance(
        source="S", mode=DataMode.LIVE, fetched_at=datetime.now(UTC), as_of=as_of, note=note
    )


@pytest.mark.parametrize(
    ("today", "stale"),
    [(date(2026, 8, 31), False), (date(2027, 1, 1), False), (date(2027, 2, 1), True)],
)
def test_data_is_stale_only_beyond_the_limit(today: date, stale: bool) -> None:
    checked = check_age(_provenance("2026-07"), max_age_months=6, today=today)

    assert checked.stale is stale
    assert ("stale: data month 2026-07" in checked.note) is stale


def test_stale_note_is_appended_to_an_existing_note() -> None:
    checked = check_age(
        _provenance("2025-01", "live data unavailable"), max_age_months=6, today=date(2026, 1, 1)
    )

    assert checked.note.startswith("live data unavailable; stale: data month 2025-01 is 12")


def test_history_lists_missing_and_short_airports() -> None:
    series = {"AAA": [1, 2, 3], "BBB": [1]}
    history = History(series, _provenance("2026-07"), ["AAA", "BBB", "CCC"], min_months=2)

    assert history.missing_codes == ["CCC"]
    assert history.short_codes == ["BBB"]
    assert history.series("aaa") == [1, 2, 3]
