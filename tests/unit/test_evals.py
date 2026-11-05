import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from fake_llm import ScriptedLlm, say

from airport_agent.agent import build_agent
from airport_agent.config import Settings
from airport_agent.dataset import Dataset
from airport_agent.llm import Message, ToolCall
from airport_agent.prompts import SYSTEM_PROMPT
from airport_agent.tools import build_registry
from evals.cases import Case, Category, Expect, load_cases
from evals.metrics import Summary, ratio, summarize
from evals.oracle import Oracle
from evals.run import Run, append_ledger, failed_metrics, read_prompt, run_cases

ACCEPTANCE_TOOLS = {"rank_region", "compare_congestion", "distance_profile", "unmet_demand"}


class AlwaysScores:
    """A bad router: every question gets an airport score for SFO."""

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: list[dict[str, Any]],
        allow_tools: bool,
    ) -> Message:
        if messages[-1].tool_outputs or not allow_tools:
            return Message("assistant", text="SFO scores 69.4.")

        return Message(
            "assistant", tool_calls=(ToolCall("c", "airport_score", {"airport": "SFO"}),)
        )


def _summary(dataset: Dataset, cases: list[Case], client: Any) -> Summary:
    outcomes = run_cases(cases, client, dataset, SYSTEM_PROMPT)

    return summarize(outcomes, build_registry(dataset))


def test_the_question_set_has_the_required_size_and_mix() -> None:
    cases = load_cases()
    turns = [t for c in cases for t in c.turns]
    categories = {c.category for c in cases}

    assert len(cases) == 40
    assert sum(c.category is Category.ADVERSARIAL for c in cases) >= 8
    assert categories == set(Category)
    assert {call.tool for t in turns for call in t.calls} >= ACCEPTANCE_TOOLS
    assert any(t.expect is Expect.ABSTAIN for t in turns)


def test_the_oracle_scores_perfectly_so_any_other_number_is_the_agent(dataset: Dataset) -> None:
    cases = load_cases()
    summary = _summary(dataset, cases, Oracle(cases))

    assert summary.failures == []
    assert all(score == 1.0 for score in summary.scores.values())


def test_a_wrong_router_is_caught_by_the_routing_checks(dataset: Dataset) -> None:
    cases = load_cases()
    summary = _summary(dataset, cases, AlwaysScores())

    assert summary.scores["routing_precision"] < 0.5
    assert summary.scores["abstention"] < 1.0
    assert any("expected tools" in failure for failure in summary.failures)
    assert ("rank_region", "airport_score") in summary.confusion


def test_every_configured_threshold_names_a_real_metric(
    dataset: Dataset, settings: Settings
) -> None:
    cases = load_cases()
    summary = _summary(dataset, cases, Oracle(cases))

    assert set(settings.eval_thresholds) <= set(summary.scores)


def test_failed_metrics_lists_those_under_their_threshold() -> None:
    summary = Summary({"a": 0.5, "b": 1.0}, Counter(), [], 1, 1, 0.0)
    run = Run("oracle", "default", "x", summary, {"a": 0.9, "b": 0.9})

    assert failed_metrics(run) == ["a"]


def test_ratio_of_nothing_is_perfect_not_a_division_error() -> None:
    assert ratio(0, 0) == 1.0
    assert ratio(1, 2) == 0.5


def test_the_ledger_only_ever_gains_rows(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"

    first = append_ledger(ledger, {"n": 1})
    before = ledger.read_text(encoding="utf-8")
    second = append_ledger(ledger, {"n": 2})

    assert (first, second) == (1, 2)
    assert ledger.read_text(encoding="utf-8").startswith(before)
    assert [json.loads(line)["n"] for line in ledger.read_text().splitlines()] == [1, 2]


def test_a_prompt_file_replaces_the_default_and_reaches_the_model(
    dataset: Dataset, tmp_path: Path
) -> None:
    file = tmp_path / "terse.txt"
    file.write_text("Be terse.", encoding="utf-8")
    prompt, name = read_prompt(file)
    llm = ScriptedLlm(say("Hello."))

    build_agent(llm, dataset, system_prompt=prompt).ask("hi")

    assert (name, llm.calls[0]["system"]) == ("terse", "Be terse.")
    assert read_prompt(None) == (SYSTEM_PROMPT, "default")
