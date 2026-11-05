"""A stand-in model that does what each case expects and words the answer from the tool output.

It scores the harness and the checks, not a prompt: with the oracle every routing and grounding
metric should be perfect, so any other number points at the agent code, not at a model.
"""

from collections.abc import Callable, Sequence
from typing import Any

from airport_agent.llm import Message, ToolCall, ToolOutput
from airport_agent.tools.result import ToolResult
from evals.cases import Case, Turn

DECLINE = "That is outside what I can answer from US airport traffic and delay data."
Renderer = Callable[[dict[str, Any]], list[str]]


def _score(data: dict[str, Any]) -> list[str]:
    airport = data["airport"]["iata"]

    return [
        f"{airport} scores {data['score']} with confidence {data['confidence']}.",
        "The score combines proxy signals, not observed demand.",
    ]


def _ranking(data: dict[str, Any]) -> list[str]:
    rows = [
        f"{r['airport']['iata']} ranks {r['rank']} with a score of {r['score']}."
        for r in data["ranking"]
    ]

    return [*rows, "Scores are proxy signals, not observed demand."]


def _congestion(data: dict[str, Any]) -> list[str]:
    first, second = data["airports"]
    worse = data["more_delayed"]
    verdict = f"{worse} is more delayed." if worse else "Both are equally delayed."

    return [
        f"{first['airport']['iata']} has {first['delay_rate']['shown']} late departures.",
        f"{second['airport']['iata']} has {second['delay_rate']['shown']} late departures.",
        verdict,
        "Delay rate is a congestion proxy.",
    ]


def _distance(data: dict[str, Any]) -> list[str]:
    return [
        f"{data['airport']['iata']} averages {data['avg_distance']['shown']} per flight.",
        "That is an average, not the share of long-haul flights.",
    ]


def _unmet(data: dict[str, Any]) -> list[str]:
    signals = ", ".join(f"{s['label'].split(' (')[0]} {s['shown']}" for s in data["signals"])

    return [
        f"{data['airport']['iata']} shows {signals}.",
        "These are signals, not observed unmet demand.",
    ]


RENDERERS: dict[str, Renderer] = {
    "airport_score": _score,
    "rank_region": _ranking,
    "compare_congestion": _congestion,
    "distance_profile": _distance,
    "unmet_demand": _unmet,
}


def render(result: ToolResult) -> list[str]:
    if result.error:
        options = f" Options: {', '.join(result.error.options)}." if result.error.options else ""

        return [f"I could not answer: {result.error.message}.{options}"]

    return RENDERERS[result.tool](result.data)


def compose(outputs: Sequence[ToolOutput]) -> str:
    results = [ToolResult.model_validate_json(output.content) for output in outputs]

    return "\n".join(line for result in results for line in render(result))


class Oracle:
    def __init__(self, cases: Sequence[Case]) -> None:
        self._turns: dict[str, Turn] = {t.question: t for c in cases for t in c.turns}

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: list[dict[str, Any]],
        allow_tools: bool,
    ) -> Message:
        last = messages[-1]

        if last.tool_outputs:
            return Message("assistant", text=compose(last.tool_outputs))

        turn = self._turn_of(messages)

        if not turn.calls or not allow_tools:
            return Message("assistant", text=DECLINE)

        calls = tuple(ToolCall(f"c{i}", c.tool, c.args) for i, c in enumerate(turn.calls))

        return Message("assistant", tool_calls=calls)

    def _turn_of(self, messages: Sequence[Message]) -> Turn:
        for message in reversed(messages):
            if message.role == "user" and message.text in self._turns:
                return self._turns[message.text]

        raise LookupError("no known question in the conversation")
