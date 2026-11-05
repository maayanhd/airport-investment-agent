from pydantic import BaseModel, Field

from airport_agent.dataset import Dataset
from airport_agent.display import miles
from airport_agent.kpis import YEAR
from airport_agent.tools.common import (
    Resolved,
    airport_ref,
    data_caveats,
    kpis_for,
    metric,
    month,
    resolve,
)
from airport_agent.tools.result import ErrorKind, ToolResult

NOT_A_SHARE = (
    "This is the average miles per departure, not the share of long-haul flights: public data "
    "has no per-route distances. An airport can mix many short flights with a few long ones."
)


class DistanceArgs(BaseModel):
    airport: str = Field(
        min_length=1, max_length=60, description="An airport code such as ANC, or a city."
    )


class DistanceProfileTool:
    name = "distance_profile"
    description = (
        "Average flight distance out of one airport compared with the long-haul threshold. Use it "
        "for questions about long-haul traffic; it gives an indicator, not a long-haul share."
    )
    input_model = DistanceArgs

    def __init__(self, data: Dataset) -> None:
        self._data = data

    def run(self, args: DistanceArgs) -> ToolResult:
        resolved = resolve(self._data, self.name, args.airport)

        if not isinstance(resolved, Resolved):
            return resolved

        kpis = kpis_for(self._data, resolved.code)
        average = kpis.avg_distance_miles

        if average is None:
            message = f"not enough consecutive traffic months for {resolved.code}"

            return ToolResult.failure(self.name, ErrorKind.INSUFFICIENT_DATA, message)

        threshold = self._data.settings.long_haul_min_miles

        return ToolResult(
            tool=self.name,
            ok=True,
            data={
                "airport": airport_ref(self._data, resolved.code),
                "avg_distance": metric(average, miles, digits=0),
                "long_haul_threshold": metric(threshold, miles, digits=0),
                "above_threshold": round(average) >= threshold,
                "traffic_through": month(kpis.traffic_through),
            },
            assumptions=[
                *resolved.assumptions,
                f"Long-haul means flights of {threshold:,.0f} miles or more (config.yaml); "
                f"the average is weighted by departures over the newest {YEAR} consecutive months.",
            ],
            caveats=[NOT_A_SHARE, *data_caveats(self._data)],
            sources=self._data.sources(),
        )
