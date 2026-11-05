"""The four acceptance questions through the real tools, a scripted model and the validator."""

from typing import Any

from fake_llm import ScriptedLlm, call, say

from airport_agent.agent import build_agent
from airport_agent.answer import Answer, Status
from airport_agent.dataset import Dataset
from airport_agent.tools import build_registry
from airport_agent.tools.result import ToolResult


def _run(dataset: Dataset, tool: str, question: str, wording: Any, **arguments: Any) -> Answer:
    """The model calls `tool`, then writes `wording(result)`, built only from that result."""
    result = build_registry(dataset).call(tool, arguments)
    llm = ScriptedLlm(call(tool, **arguments), say(wording(result)))
    agent = build_agent(llm, dataset)

    return agent.ask(question)


def _ranking(result: ToolResult) -> str:
    rows = result.data["ranking"]

    return "; ".join(f"{r['rank']}. {r['airport']['iata']} {r['score']}" for r in rows)


def _congestion(result: ToolResult) -> str:
    first, second = result.data["airports"]

    return (
        f"{first['airport']['iata']} has {first['delay_rate']['shown']} late departures and "
        f"{second['airport']['iata']} has {second['delay_rate']['shown']}; "
        f"{result.data['more_delayed']} is more delayed "
        f"(gap {result.data['delay_rate_gap']['shown']})."
    )


def _distance(result: ToolResult) -> str:
    data = result.data

    return (
        f"{data['airport']['iata']} averages {data['avg_distance']['shown']} per flight against "
        f"{data['long_haul_threshold']['shown']} for long-haul."
    )


def _unmet(result: ToolResult) -> str:
    signals = ", ".join(f"{s['name']} {s['shown']}" for s in result.data["signals"])

    return f"{result.data['airport']['iata']}: {signals}."


def test_region_ranking_question(dataset: Dataset) -> None:
    answer = _run(
        dataset, "rank_region", "Best New England airports?", _ranking,
        region="New England", top_n=3,
    )  # fmt: skip

    assert answer.status is Status.ANSWERED
    assert "1. PSM" in answer.text
    assert any("Region" in a for a in answer.notes.assumptions)


def test_congestion_comparison_question(dataset: Dataset) -> None:
    answer = _run(
        dataset, "compare_congestion", "Congestion at LA vs Santa Ana?", _congestion,
        airport_a="LA", airport_b="Santa Ana",
    )  # fmt: skip

    assert answer.status is Status.ANSWERED
    assert any('"LA" read as LAX' in a for a in answer.notes.assumptions)
    assert any("congestion proxy" in c for c in answer.notes.caveats)


def test_long_haul_question_states_it_is_not_a_share(dataset: Dataset) -> None:
    answer = _run(
        dataset, "distance_profile", "Long-haul share out of Anchorage?", _distance,
        airport="Anchorage",
    )  # fmt: skip

    assert answer.status is Status.ANSWERED
    assert any("not the share of long-haul flights" in c for c in answer.notes.caveats)


def test_unmet_demand_question_labels_the_signals_as_proxies(dataset: Dataset) -> None:
    answer = _run(
        dataset, "unmet_demand", "Why is there unmet demand at SFO?", _unmet, airport="SFO"
    )  # fmt: skip

    assert answer.status is Status.ANSWERED
    assert any("cannot be observed" in c for c in answer.notes.caveats)
    assert any("BTS T-100" in s for s in answer.notes.sources)


def test_a_follow_up_for_another_airport_works_end_to_end(dataset: Dataset) -> None:
    registry = build_registry(dataset)
    sfo = registry.call("unmet_demand", {"airport": "SFO"})
    bos = registry.call("unmet_demand", {"airport": "BOS"})
    llm = ScriptedLlm(
        call("unmet_demand", airport="SFO"),
        say(_unmet(sfo)),
        call("unmet_demand", airport="BOS"),
        say(_unmet(bos)),
    )
    agent = build_agent(llm, dataset)

    agent.ask("Why is there unmet demand at SFO?")
    answer = agent.ask("and what about BOS?")

    assert answer.status is Status.ANSWERED
    assert answer.results[0].data["airport"]["iata"] == "BOS"
    assert "airports SFO" in llm.calls[2]["system"]
