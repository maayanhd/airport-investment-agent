from pydantic import BaseModel, Field

from airport_agent.dataset import Dataset
from airport_agent.display import load_percent, minutes, percent, points
from airport_agent.kpis import AirportKpis
from airport_agent.tools.common import (
    PROXY_CAVEAT,
    Resolved,
    airport_ref,
    data_caveats,
    kpis_for,
    metric,
    month,
    resolve_all,
)
from airport_agent.tools.result import ErrorKind, ToolResult

NOT_ONLY_CONGESTION = (
    "Delays also come from weather and airline problems, so a higher delay rate points to more "
    "congestion but does not prove it."
)


class CompareArgs(BaseModel):
    airport_a: str = Field(min_length=1, max_length=60, description="First airport or city.")
    airport_b: str = Field(min_length=1, max_length=60, description="Second airport or city.")


def _row(data: Dataset, code: str, kpis: AirportKpis) -> dict[str, object]:
    return {
        "airport": airport_ref(data, code),
        "delay_rate": metric(kpis.delay_rate, percent),
        "cancel_rate": metric(kpis.cancel_rate, percent),
        "avg_delay_minutes": metric(kpis.avg_delay_minutes, minutes, digits=1),
        "load_factor": metric(kpis.load_factor, load_percent, digits=1),
        "delays_through": month(kpis.delays_through),
    }


class CompareCongestionTool:
    name = "compare_congestion"
    description = (
        "Compare how congested two US airports are, using the share of flights departing 15+ "
        "minutes late as the main proxy, with cancellations, average delay and load factor as "
        "context. Use it for 'is X more congested than Y'."
    )
    input_model = CompareArgs

    def __init__(self, data: Dataset) -> None:
        self._data = data

    def run(self, args: CompareArgs) -> ToolResult:
        resolved = resolve_all(self._data, self.name, [args.airport_a, args.airport_b])

        if not isinstance(resolved, list):
            return resolved

        first, second = resolved

        if first.code == second.code:
            message = f"both names mean {first.code}; name two different airports"

            return ToolResult.failure(self.name, ErrorKind.BAD_ARGUMENTS, message)

        kpis = [kpis_for(self._data, r.code) for r in resolved]
        lacking = [r.code for r, k in zip(resolved, kpis, strict=True) if k.delay_rate is None]

        if lacking:
            message = f"not enough delay data to compare: {', '.join(lacking)}"

            return ToolResult.failure(self.name, ErrorKind.INSUFFICIENT_DATA, message)

        return self._result(resolved, kpis)

    def _result(self, resolved: list[Resolved], kpis: list[AirportKpis]) -> ToolResult:
        rates = [k.delay_rate or 0.0 for k in kpis]
        gap = abs(rates[0] - rates[1])
        worse = None if gap == 0 else resolved[rates.index(max(rates))].code

        return ToolResult(
            tool=self.name,
            ok=True,
            data={
                "airports": [
                    _row(self._data, r.code, k) for r, k in zip(resolved, kpis, strict=True)
                ],
                "more_delayed": worse,
                "delay_rate_gap": metric(gap, points),
            },
            assumptions=[
                *(a for r in resolved for a in r.assumptions),
                "Congestion is measured by the share of operated flights departing 15+ minutes "
                "late over the last 12 months; load factor is shown as context only.",
            ],
            caveats=[PROXY_CAVEAT, NOT_ONLY_CONGESTION, *data_caveats(self._data)],
            sources=self._data.sources(),
        )
