from pydantic import BaseModel, Field

from airport_agent.dataset import Dataset
from airport_agent.display import FORMATS, LABELS, growth
from airport_agent.kpis import AirportKpis
from airport_agent.scoring import AirportScore, Component
from airport_agent.tools.common import (
    PROXY_CAVEAT,
    RATIO_DIGITS,
    Resolved,
    airport_ref,
    data_caveats,
    kpis_for,
    metric,
    month,
    partial_caveat,
    resolve,
    score_for,
)
from airport_agent.tools.result import ErrorKind, ToolResult

NOT_OBSERVED = (
    "Unmet demand cannot be observed in public data. These are signals that usually go with it: "
    "demand growing, seats full, demand outgrowing seats, and flights running late."
)


class UnmetDemandArgs(BaseModel):
    airport: str = Field(
        min_length=1, max_length=60, description="An airport code such as SFO, or a city."
    )


def _signal(component: Component, threshold: float) -> dict[str, object]:
    return {
        "name": component.name,
        "label": LABELS[component.name],
        "value": round(component.value, RATIO_DIGITS),
        "shown": FORMATS[component.name](component.value),
        "strong": component.scaled >= threshold,
    }


class UnmetDemandTool:
    name = "unmet_demand"
    description = (
        "Signals that an airport may have unmet demand: passenger growth, load factor, passenger "
        "growth minus seat growth, and delay pressure, each marked strong or not. Use it for "
        "'why might X be a good expansion candidate' or 'is there unmet demand at X'."
    )
    input_model = UnmetDemandArgs

    def __init__(self, data: Dataset) -> None:
        self._data = data

    def run(self, args: UnmetDemandArgs) -> ToolResult:
        resolved = resolve(self._data, self.name, args.airport)

        if not isinstance(resolved, Resolved):
            return resolved

        kpis = kpis_for(self._data, resolved.code)
        result = score_for(self._data, kpis)

        if result.score is None:
            return ToolResult.failure(self.name, ErrorKind.INSUFFICIENT_DATA, result.reason)

        return self._result(resolved, kpis, result)

    def _result(self, resolved: Resolved, kpis: AirportKpis, result: AirportScore) -> ToolResult:
        threshold = self._data.settings.strong_signal_scaled
        signals = [_signal(c, threshold) for c in result.components]

        return ToolResult(
            tool=self.name,
            ok=True,
            data={
                "airport": airport_ref(self._data, resolved.code),
                "signals": signals,
                "strong_signals": sum(bool(s["strong"]) for s in signals),
                "total_signals": len(signals),
                "missing": list(result.missing),
                "seat_growth": metric(kpis.seat_growth, growth),
                "traffic_through": month(kpis.traffic_through),
                "delays_through": month(kpis.delays_through),
            },
            assumptions=[
                *resolved.assumptions,
                f"A signal is strong when it sits at or above {threshold:.0%} of its fixed range "
                "in config.yaml (score_bounds), so strength does not depend on other airports.",
            ],
            caveats=[
                NOT_OBSERVED,
                PROXY_CAVEAT,
                *partial_caveat(result, resolved.code, self._data),
                *data_caveats(self._data),
            ],
            sources=self._data.sources(),
        )
