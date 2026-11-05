from pydantic import BaseModel, Field

from airport_agent.dataset import Dataset
from airport_agent.display import FORMATS, LABELS, describe_scoring
from airport_agent.scoring import AirportScore
from airport_agent.tools.common import (
    PROXY_CAVEAT,
    RATIO_DIGITS,
    Resolved,
    airport_ref,
    data_caveats,
    kpis_for,
    month,
    partial_caveat,
    resolve,
    score_for,
)
from airport_agent.tools.result import ErrorKind, ToolError, ToolResult


class ScoreArgs(BaseModel):
    airport: str = Field(
        min_length=1,
        max_length=60,
        description="An airport code such as BOS, or a common name such as 'LA' or 'Santa Ana'.",
    )


def _components(result: AirportScore) -> list[dict[str, object]]:
    return [
        {
            "name": c.name,
            "label": LABELS[c.name],
            "value": round(c.value, RATIO_DIGITS),
            "shown": FORMATS[c.name](round(c.value, RATIO_DIGITS)),
            "scaled": round(c.scaled, 2),
            "weight": c.weight,
            "points": round(c.points, 1),
        }
        for c in result.components
    ]


class AirportScoreTool:
    name = "airport_score"
    description = (
        "Expansion score from 0 to 100 for one US airport, with every component (value, weight, "
        "points), the confidence, what is missing, and the data months. Use it for 'how strong "
        "is X as an expansion candidate' and for follow-ups about one airport."
    )
    input_model = ScoreArgs

    def __init__(self, data: Dataset) -> None:
        self._data = data

    def run(self, args: ScoreArgs) -> ToolResult:
        resolved = resolve(self._data, self.name, args.airport)

        if not isinstance(resolved, Resolved):
            return resolved

        kpis = kpis_for(self._data, resolved.code)
        result = score_for(self._data, kpis)
        data = {
            "airport": airport_ref(self._data, resolved.code),
            "score": None if result.score is None else round(result.score, 1),
            "confidence": round(result.confidence, 2),
            "components": _components(result),
            "missing": list(result.missing),
            "traffic_through": month(kpis.traffic_through),
            "delays_through": month(kpis.delays_through),
        }
        failure = (
            None
            if result.score is not None
            else ToolError(kind=ErrorKind.INSUFFICIENT_DATA, message=result.reason)
        )

        return ToolResult(
            tool=self.name,
            ok=failure is None,
            data=data,
            assumptions=[*resolved.assumptions, *describe_scoring(self._data.settings)],
            caveats=[
                PROXY_CAVEAT,
                *partial_caveat(result, resolved.code, self._data),
                *data_caveats(self._data),
            ],
            sources=self._data.sources(),
            error=failure,
        )
