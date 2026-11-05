import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from fake_llm import ScriptedLlm, say
from pydantic import ValidationError

from airport_agent.agent import build_agent
from airport_agent.answer import Answer, Notes, Status
from airport_agent.config import Settings
from airport_agent.dataset import Dataset
from airport_agent.llm import Message, ToolCall
from airport_agent.prompts import SYSTEM_PROMPT
from airport_agent.tools import build_registry
from airport_agent.tools.result import ToolResult
from evals import run as run_module
from evals.cases import Case, Category, Expect, Turn, load_cases
from evals.metrics import (
    Outcome,
    Summary,
    headline,
    is_abstention,
    quotes,
    ratio,
    summarize,
)
from evals.oracle import Oracle
from evals.run import Run, append_ledger, failed_metrics, parse_args, read_prompt, run_cases

ACCEPTANCE_TOOLS = {"rank_region", "compare_congestion", "distance_profile", "unmet_demand"}
MAX_PLAIN = 10


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

        call = ToolCall("c", "airport_score", {"airport": "SFO"})

        return Message("assistant", tool_calls=(call,))


class NeverUsesTools:
    """A model that answers everything confidently from its own head, with no figures."""

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: list[dict[str, Any]],
        allow_tools: bool,
    ) -> Message:
        return Message("assistant", text="Casper is an excellent expansion candidate.")


def _summary(dataset: Dataset, cases: list[Case], client: Any) -> Summary:
    outcomes = run_cases(cases, client, dataset, SYSTEM_PROMPT)

    return summarize(outcomes, build_registry(dataset), MAX_PLAIN)


def _outcome(turn: Turn, answer: Answer) -> Outcome:
    case = Case(id="x", category=Category.RANK, turns=[turn])

    return Outcome(case, turn, answer, 1, 0.0)


def test_the_question_set_has_the_required_size_and_mix() -> None:
    cases = load_cases()
    turns = [t for c in cases for t in c.turns]
    categories = {c.category for c in cases}

    assert len(cases) == 40
    assert sum(c.category is Category.ADVERSARIAL for c in cases) >= 8
    assert categories == set(Category)
    assert {call.tool for t in turns for call in t.calls} >= ACCEPTANCE_TOOLS
    assert any(t.expect is Expect.ABSTAIN for t in turns)


def test_every_expected_call_works_the_way_its_case_says(dataset: Dataset) -> None:
    """A mistyped argument would otherwise silently drop a case from the gold check."""
    registry = build_registry(dataset)

    for case in load_cases():
        for turn in case.turns:
            results = [registry.call(call.tool, call.args) for call in turn.calls]

            if turn.expect is Expect.ANSWER and turn.calls:
                assert all(r.ok and headline(r) for r in results), (case.id, turn.question)
            elif turn.calls:
                assert not any(r.ok for r in results), (case.id, turn.question)


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


def test_a_model_that_answers_without_tools_does_not_pass_the_abstention_check(
    dataset: Dataset,
) -> None:
    cases = [c for c in load_cases() if c.id in {"adv-02", "scope-01"}]
    summary = _summary(dataset, cases, NeverUsesTools())

    assert summary.scores["abstention"] == 0.0
    assert len(summary.failures) == 2


def test_a_reply_with_figures_is_not_an_abstention() -> None:
    plain = Answer(Status.ANSWERED, "I cannot help with that. Try a region.")
    figures = Answer(Status.ANSWERED, "Casper grew 12.5% last year.")
    confident = Answer(Status.ANSWERED, "Casper is an excellent expansion candidate.")
    refused = Answer(Status.ABSTAINED, "I could not verify an answer.")
    small_count = Answer(Status.ANSWERED, "Which of the 2 airports do you mean?")

    assert is_abstention(plain, MAX_PLAIN)
    assert is_abstention(small_count, MAX_PLAIN)
    assert not is_abstention(figures, MAX_PLAIN)
    assert not is_abstention(confident, MAX_PLAIN)
    assert is_abstention(refused, MAX_PLAIN)


@pytest.mark.parametrize(
    ("figure", "text", "found"),
    [
        ("7", "It has 17% and 7 airports", True),
        ("7", "It has 17% of flights", False),
        ("69.4", "scores 169.4 overall", False),
        ("69.4", "scores 69.4.", True),
        ("69.4", "scores 69.45", False),
        ("BOS", "BOSTON is big", False),
        ("BOS", "bos leads", True),
    ],
)
def test_a_figure_must_appear_as_a_whole_token(figure: str, text: str, found: bool) -> None:
    assert quotes(text, figure) is found


def test_a_case_whose_expected_call_fails_is_a_failure_not_a_skip(dataset: Dataset) -> None:
    broken = Case.model_validate(
        {
            "id": "broken",
            "category": "rank",
            "question": "Best airports in Narnia?",
            "calls": [{"tool": "rank_region", "args": {"region": "Narnia"}}],
        }
    )
    answer = Answer(Status.ANSWERED, "Narnia is great.", Notes())
    summary = summarize([_outcome(broken.turns[0], answer)], build_registry(dataset), MAX_PLAIN)

    assert summary.scores["gold_match"] == 0.0
    assert any("fails, so it has no gold figures" in f for f in summary.failures)


@pytest.mark.parametrize(
    ("tool", "data"),
    [
        ("rank_region", {"ranking": []}),
        ("unmet_demand", {"signals": []}),
        ("airport_score", {}),
        ("distance_profile", {"avg_distance": None}),
        ("not_a_tool", {"anything": 1}),
    ],
)
def test_a_result_with_no_headline_figure_gives_an_empty_list_not_a_crash(
    tool: str, data: dict[str, Any]
) -> None:
    assert headline(ToolResult(tool=tool, ok=True, data=data)) == []


def test_one_problem_turn_is_one_failure_line(dataset: Dataset) -> None:
    cases = [c for c in load_cases() if c.id == "rank-01"]
    summary = _summary(dataset, cases, NeverUsesTools())

    assert len(summary.failures) == 1
    assert "expected tools" in summary.failures[0]
    assert "expected an answer" not in summary.failures[0]  # status is answered, just not routed


def test_disclosures_read_the_structured_sources_not_their_wording(dataset: Dataset) -> None:
    cases = [c for c in load_cases() if c.id == "compare-01"]
    summary = _summary(dataset, cases, Oracle(cases))

    assert summary.scores["disclosures"] == 1.0


def test_a_misspelled_key_in_a_case_is_an_error() -> None:
    with pytest.raises(ValidationError):
        Case.model_validate(
            {"id": "t", "category": "rank", "question": "q?", "must_stat": ["data_month"]}
        )

    with pytest.raises(ValidationError):
        Case.model_validate({"id": "t", "category": "rank", "question": "q?", "call": []})


def test_a_case_cannot_have_both_a_question_and_turns() -> None:
    with pytest.raises(ValidationError, match="not both"):
        Case.model_validate(
            {"id": "t", "category": "rank", "question": "q?", "turns": [{"question": "r?"}]}
        )


def test_every_configured_threshold_names_a_real_metric(
    dataset: Dataset, settings: Settings
) -> None:
    cases = load_cases()
    summary = _summary(dataset, cases, Oracle(cases))

    assert set(settings.eval_thresholds) <= set(summary.scores)


def test_failed_metrics_lists_those_under_their_threshold() -> None:
    summary = Summary({"a": 0.5, "b": 1.0}, Counter(), [], 1, 1, 0.0)
    run = Run("oracle", False, "default", "x", "abc", summary, {"a": 0.9, "b": 0.9}, {})

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


def test_a_dirty_tree_is_marked_in_the_sha(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_git(*args: str) -> str | None:
        return "abc1234" if args[0] == "rev-parse" else " M evals/run.py"

    monkeypatch.setattr(run_module, "_git", fake_git)
    assert run_module.git_sha() == "abc1234-dirty"

    monkeypatch.setattr(run_module, "_git", lambda *a: "abc1234" if a[0] == "rev-parse" else "")
    assert run_module.git_sha() == "abc1234"

    monkeypatch.setattr(run_module, "_git", lambda *a: None)
    assert run_module.git_sha() == "unknown"


def test_a_prompt_cannot_be_compared_with_a_client_that_ignores_it(tmp_path: Path) -> None:
    file = tmp_path / "terse.txt"
    file.write_text("Be terse.", encoding="utf-8")

    with pytest.raises(SystemExit):
        parse_args(["--client", "oracle", "--prompt", str(file)])

    assert parse_args(["--client", "oracle"]).prompt is None


def test_the_ledger_row_says_whether_a_model_was_involved(settings: Settings) -> None:
    summary = Summary({"a": 1.0}, Counter(), [], 1, 1, 0.0)
    run = Run("oracle", False, "default", "x", "abc", summary, {}, {"rank": 1})
    row = run_module.ledger_row(run, settings)

    assert row["model_backed"] is False
    assert row["sha"] == "abc"


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
