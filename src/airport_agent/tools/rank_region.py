import re

from pydantic import BaseModel, Field

from airport_agent.config import Settings
from airport_agent.dataset import Dataset
from airport_agent.display import describe_scoring
from airport_agent.domain import STATE_PATTERN, search_words
from airport_agent.scoring import rank
from airport_agent.tools.common import (
    PROXY_CAVEAT,
    airport_ref,
    data_caveats,
    kpis_for,
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


def _states(settings: Settings, region: str) -> list[str] | None:
    named = settings.regions.get(" ".join(search_words(region)))

    if named:
        return named

    code = region.strip().upper()

    return [code] if re.fullmatch(STATE_PATTERN, code) else None


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
                f"unknown region {args.region!r}; use a state code or one of the listed regions",
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

        data = {
            "region": args.region,
            "states": states,
            "covered_airports": len(airports),
            "ranking": [
                {
                    "rank": position,
                    "airport": airport_ref(self._data, s.iata),
                    "score": round(s.score or 0.0, 1),
                    "confidence": round(s.confidence, 2),
                    "complete": not s.missing,
                }
                for position, s in enumerate(scored[: args.top_n], start=1)
            ],
            "not_scored": [{"iata": s.iata, "reason": s.reason} for s in scores if s.score is None],
        }
        partial = [s for s in scored[: args.top_n] if s.missing]

        return ToolResult(
            tool=self.name,
            ok=True,
            data=data,
            assumptions=[
                f"Region {args.region!r} is the states {', '.join(states)}.",
                *describe_scoring(self._data.settings),
            ],
            caveats=[
                PROXY_CAVEAT,
                *(c for s in partial for c in partial_caveat(s, s.iata, self._data)),
                *data_caveats(self._data),
            ],
            sources=self._data.sources(),
        )
