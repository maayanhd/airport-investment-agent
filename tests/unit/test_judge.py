import json
import os
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fake_llm import ScriptedLlm, say
from pydantic import ValidationError

from airport_agent.answer import Answer, Status
from airport_agent.config import ModelPrice, Settings
from airport_agent.env_file import load_env_file
from airport_agent.llm import LlmError, Usage
from evals import run as run_module
from evals.cases import load_cases
from evals.cost import cost_usd
from evals.judge import (
    CRITERIA,
    Grade,
    LabelledAnswer,
    agreement,
    calibrate,
    judge,
    load_labels,
    mean_scores,
    parse_grade,
)
from evals.metrics import Outcome, Summary
from evals.run import Run, judge_outcomes, parse_args


def _scores(value: int) -> dict[str, int]:
    return dict.fromkeys(CRITERIA, value)


def _reply(value: int = 4) -> str:
    return json.dumps({"scores": _scores(value), "evidence": {"tone": "plain"}})


def test_usage_adds_up_what_each_reply_reports() -> None:
    usage = Usage()

    usage.add(SimpleNamespace(usage=SimpleNamespace(input_tokens=100, output_tokens=20)))
    usage.add(SimpleNamespace())  # a reply that reports nothing

    assert (usage.requests, usage.input_tokens, usage.output_tokens) == (2, 100, 20)


def test_cost_is_tokens_times_the_price_per_million() -> None:
    usage = Usage(input_tokens=2_000_000, output_tokens=500_000)

    assert cost_usd(usage, ModelPrice(input_usd_per_million=0.1, output_usd_per_million=0.5)) == (
        pytest.approx(0.45)
    )


def test_a_grade_needs_every_criterion_in_range() -> None:
    assert parse_grade(_reply()) is not None

    for bad in ({"scores": {"tone": 5}}, {"scores": _scores(6)}, {"scores": _scores(0)}):
        with pytest.raises(ValidationError):
            Grade.model_validate(bad)


def test_a_grade_is_found_inside_chatter_and_garbage_gives_none() -> None:
    assert parse_grade(f"Here you go:\n{_reply(3)}\nThanks") is not None
    assert parse_grade("I would say it is good.") is None
    assert parse_grade('{"scores": ') is None
    assert parse_grade(json.dumps({"scores": _scores(9)})) is None


def test_the_judge_sees_the_rubric_and_the_data_but_no_tools() -> None:
    llm = ScriptedLlm(say(_reply()))

    grade = judge(llm, "How is BOS?", "BOS scores 62.2.", '{"score": 62.2}')

    call = llm.calls[0]
    assert grade is not None
    assert "Judge rubric" in call["system"]
    assert call["tools"] == [] and call["allow_tools"] is False
    assert "62.2" in call["messages"][0].text and "How is BOS?" in call["messages"][0].text


def test_an_unreachable_or_confused_judge_gives_no_grade_not_a_crash() -> None:
    assert judge(ScriptedLlm(LlmError("down")), "q", "a", "d") is None
    assert judge(ScriptedLlm(say("no idea")), "q", "a", "d") is None


def test_mean_scores_are_per_criterion() -> None:
    grades = [Grade(scores=_scores(2)), Grade(scores=_scores(4))]

    assert mean_scores(grades) == dict.fromkeys(CRITERIA, 3.0)
    assert mean_scores([]) == {}


def test_agreement_counts_scores_within_one_point() -> None:
    pairs = [(_scores(4), _scores(5)), (_scores(1), _scores(4)), (None, _scores(3))]

    assert agreement(pairs, tolerance=1) == dict.fromkeys(CRITERIA, 1 / 3)
    assert agreement(pairs, tolerance=3) == dict.fromkeys(CRITERIA, 2 / 3)
    assert agreement([], tolerance=1) == {}


def _label(human: int) -> LabelledAnswer:
    return LabelledAnswer(question="q", answer="a", data="d", human=Grade(scores=_scores(human)))


def test_calibration_needs_enough_labels_and_enough_agreement() -> None:
    labels = [_label(4), _label(4)]

    agrees = calibrate(
        ScriptedLlm(say(_reply(4)), say(_reply(5))),
        labels,
        needed_labels=2,
        needed_agreement=0.8,
        tolerance=1,
    )
    too_few = calibrate(
        ScriptedLlm(say(_reply(4)), say(_reply(4))),
        labels,
        needed_labels=3,
        needed_agreement=0.8,
        tolerance=1,
    )
    disagrees = calibrate(
        ScriptedLlm(say(_reply(1)), say(_reply(1))),
        labels,
        needed_labels=2,
        needed_agreement=0.8,
        tolerance=1,
    )

    assert agrees.is_calibrated
    assert not too_few.is_calibrated
    assert not disagrees.is_calibrated


def test_a_judge_that_cannot_grade_counts_as_disagreeing_with_the_labels() -> None:
    result = calibrate(
        ScriptedLlm(LlmError("down"), say("nonsense")),
        [_label(5), _label(1)],
        needed_labels=2,
        needed_agreement=0.5,
        tolerance=1,
    )

    assert result.by_criterion == dict.fromkeys(CRITERIA, 0.0)


def test_the_shipped_label_file_loads() -> None:
    assert isinstance(load_labels(), list)


def test_the_label_file_rejects_a_misspelled_key(tmp_path: Path) -> None:
    path = tmp_path / "labels.yaml"
    path.write_text("- {question: q, answer: a, data: d, human: {scores: {}}, extra: 1}\n")

    with pytest.raises(ValidationError):
        load_labels(path)


def _outcome(status: Status) -> Outcome:
    case = load_cases()[0]

    return Outcome(case, case.turns[0], Answer(status, "text"), 1, 0.0)


def test_only_the_models_own_prose_is_judged() -> None:
    outcomes = [
        _outcome(Status.ANSWERED),
        _outcome(Status.DATA_ONLY),
        _outcome(Status.ABSTAINED),
        _outcome(Status.ANSWERED),
    ]
    llm = ScriptedLlm(say(_reply()), say("not json"))

    grades, ungraded = judge_outcomes(outcomes, llm)  # type: ignore[arg-type]

    assert (len(grades), ungraded, len(llm.calls)) == (1, 1, 2)


def test_the_oracle_has_no_prose_to_judge() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--client", "oracle", "--judge"])


def _run(settings: Settings, **extra: Any) -> Run:
    summary = Summary({"a": 1.0}, Counter(), [], 4, 8, 0.0)

    return Run("anthropic", True, "default", "x", "abc", summary, {}, {}, **extra)


def test_the_report_and_ledger_carry_tokens_cost_and_judge_scores(settings: Settings) -> None:
    run = _run(
        settings,
        usage=Usage(requests=8, input_tokens=1_000_000, output_tokens=100_000),
        judged=(Grade(scores=_scores(4)),),
        judge_usage=Usage(requests=1, input_tokens=10_000, output_tokens=500),
        unjudged=1,
    )

    report = run_module.format_report(run, settings)
    row = run_module.ledger_row(run, settings)

    assert "agent $0.1500" in report and "per question" in report
    assert "1 answers graded, 1 not" in report
    assert row["agent_cost_usd"] == pytest.approx(0.15)
    assert row["cost_usd"] == pytest.approx(0.175)  # plus the judge's 10k in and 500 out
    assert row["tokens"] == {"requests": 8, "input_tokens": 1_000_000, "output_tokens": 100_000}
    assert row["judge"]["graded"] == 1  # type: ignore[index]


def test_a_run_without_a_model_says_it_cost_nothing(settings: Settings) -> None:
    run = _run(settings)
    row = run_module.ledger_row(run, settings)

    assert "Cost: none" in run_module.format_report(run, settings)
    assert row["cost_usd"] is None and row["judge"] is None


def test_one_criterion_the_judge_gets_wrong_fails_calibration_despite_a_good_average() -> None:
    wrong_on_one = {**_scores(4), "no_invented_info": 1}
    reply = json.dumps({"scores": wrong_on_one})
    labels = [_label(4)] * 2

    result = calibrate(
        ScriptedLlm(say(reply), say(reply)),
        labels,
        needed_labels=2,
        needed_agreement=0.8,
        tolerance=1,
    )

    assert result.by_criterion["tone"] == 1.0
    assert not result.is_calibrated


def test_too_few_labels_exit_1_without_building_the_judge(
    no_env_file: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # a built judge would raise

    assert run_module.main(["--calibrate"]) == 1
    assert "NOT calibrated" in capsys.readouterr().out


def test_a_missing_judge_key_stops_the_run_before_any_paid_call(
    no_env_file: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")  # the agent could be built...
    monkeypatch.setattr(run_module.AnthropicClient, "from_settings", _fail_for_judge)

    assert run_module.main(["--client", "anthropic", "--judge", "--no-ledger"]) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().out


def _fail_for_judge(settings: Settings, *args: Any, model: str | None = None) -> Any:
    if model:  # ...but the judge model has no key, and nothing may have been asked yet
        raise run_module.MissingApiKeyError("set ANTHROPIC_API_KEY to use the real model")

    raise AssertionError("the agent must not be built before the judge")


def test_calibration_cannot_be_mixed_with_a_run() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--calibrate", "--client", "anthropic"])


@pytest.fixture
def no_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never read the real .env in a test: it holds a paid key."""
    monkeypatch.setattr(run_module, "load_env_file", lambda: [])


def test_a_missing_key_is_a_message_not_a_traceback(
    no_env_file: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    assert run_module.main(["--client", "anthropic", "--no-ledger"]) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().out


def test_the_runner_reads_the_key_from_the_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=from-file\n")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")  # so teardown restores the real state
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setattr(run_module, "load_env_file", lambda: load_env_file(env))

    assert run_module.main(["--calibrate"]) == 1  # too few labels: nothing is sent
    assert os.environ["ANTHROPIC_API_KEY"] == "from-file"
