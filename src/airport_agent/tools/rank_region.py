import re

from pydantic import BaseModel, Field

from airport_agent.config import Settings
from airport_agent.dataset import Dataset
from airport_agent.display import describe_scoring, passengers
from airport_agent.domain import STATE_PATTERN, search_words
from airport_agent.scoring import AirportScore, rank
from airport_agent.tools.common import (
    PROXY_CAVEAT,
    SIZE_CAVEAT,
    airport_ref,
    data_caveats,
    kpis_for,
    metric,
    partial_caveat,
    score_for,
)
from airport_agent.tools.result import ErrorKind, ToolResult


class RankArgs(BaseModel):
    region: str = Field(
        min_length=1,
        max_length=40,
        description="A region such as 'New England', or a two-letter state code such as 'AK'.",
    )
    top_n: int = Field(default=5, ge=1, le=20, description="How many airports to return.")


STATE_PREFIX = "state "


def _states(settings: Settings, region: str) -> list[str] | None:
    """A configured region, or a state code. A code that is also a nickname ("LA") needs the
    explicit form "state LA", so it is never read as a state by accident."""
    key = " ".join(search_words(region))
    named = settings.regions.get(key)

    if named:
        return named

    explicit = key.startswith(STATE_PREFIX)
    code = key.removeprefix(STATE_PREFIX).upper()

    if not re.fullmatch(STATE_PATTERN, code) or (key in settings.airport_aliases and not explicit):
        return None

    return [code]


ORDER_RULE = (
    "Airports with every score component are ranked before airports with partial data, even when "
    "a partial score is higher, because a missing component changes what the score means."
)


def _row(data: Dataset, position: int, score: AirportScore) -> dict[str, object]:
    return {
        "rank": position,
        "airport": airport_ref(data, score.iata),
        "passengers_12m": metric(data.passengers_12m(score.iata), passengers, 0),
        "score": round(score.score or 0.0, 1),
        "confidence": round(score.confidence, 2),
        "complete": not score.missing,
    }


def _omitted(hidden: list[AirportScore]) -> list[str]:
    """What fell below the cut, so a strong candidate is not dropped silently."""
    if not hidden:
        return []

    partial = [
        f"{s.iata} (score {s.score:.1f}, confidence {s.confidence:.0%}) is not shown: it has "
        "partial data and is ranked after every complete score."
        for s in hidden
        if s.missing
    ]
    codes = ", ".join(s.iata for s in hidden)

    return [f"{len(hidden)} more scored airports are not shown: {codes}.", *partial]


def _assumptions(data: Dataset, region: str, states: list[str]) -> list[str]:
    reading = f"Region {region!r} is the states {', '.join(states)}."

    return [reading, ORDER_RULE, *describe_scoring(data.settings)]


class RankRegionTool:
    name = "rank_region"
    description = (
        "Rank the covered airports of a region (for example New England) or a state by expansion "
        "score, best first, with each score and confidence. Use it for 'which airports in X "
        "have the strongest case'."
    )
    input_model = RankArgs

    def __init__(self, data: Dataset) -> None:
        self._data = data

    def run(self, args: RankArgs) -> ToolResult:
        states = _states(self._data.settings, args.region)

        if states is None:
            return ToolResult.failure(
                self.name,
                ErrorKind.BAD_ARGUMENTS,
                f"unknown region {args.region!r}; use a state code (for a nickname like LA, write "
                "'state LA') or one of the listed regions",
                list(self._data.settings.regions),
            )

        airports = self._data.catalog.in_states(states)

        if not airports:
            message = f"no covered airports in {', '.join(states)}"

            return ToolResult.failure(self.name, ErrorKind.NOT_COVERED, message)

        scores = rank(score_for(self._data, kpis_for(self._data, a.iata)) for a in airports)
        scored = [s for s in scores if s.score is not None]

        if not scored:
            message = "no airport in the region has enough data for a score"

            return ToolResult.failure(self.name, ErrorKind.INSUFFICIENT_DATA, message)

        return self._result(args, states, scores, scored)

    def _result(
        self,
        args: RankArgs,
        states: list[str],
        scores: list[AirportScore],
        scored: list[AirportScore],
    ) -> ToolResult:
        shown = scored[: args.top_n]
        partial = (c for s in shown if s.missing for c in partial_caveat(s, s.iata, self._data))

        return ToolResult(
            tool=self.name,
            ok=True,
            data={
                "region": args.region,
                "states": states,
                "covered_airports": len(scores),
                "ranking": [_row(self._data, n, s) for n, s in enumerate(shown, start=1)],
                "not_scored": [
                    {"iata": s.iata, "reason": s.reason} for s in scores if s.score is None
                ],
            },
            assumptions=_assumptions(self._data, args.region, states),
            caveats=[
                PROXY_CAVEAT,
                SIZE_CAVEAT,
                *partial,
                *_omitted(scored[args.top_n :]),
                *data_caveats(self._data),
            ],
            sources=self._data.sources(),
        )
