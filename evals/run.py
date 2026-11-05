"""Run the question set through the agent and score it: `python -m evals.run --client oracle`.

The data always comes from the committed snapshots. The oracle needs no network and no key.
`--client anthropic` calls the real model (needs ANTHROPIC_API_KEY and spends money), and
`--judge` adds a second paid model that grades the prose. `--calibrate` checks that judge
against hand labels.
"""

import argparse
import hashlib
import json
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from airport_agent.agent import build_agent
from airport_agent.answer import Status
from airport_agent.anthropic_client import AnthropicClient, MissingApiKeyError
from airport_agent.config import Settings, load_settings
from airport_agent.data.http import HttpFetcher
from airport_agent.data.snapshots import SnapshotStore
from airport_agent.dataset import Dataset, load_dataset
from airport_agent.env_file import load_env_file
from airport_agent.llm import LlmClient, Message, Usage
from airport_agent.prompts import SYSTEM_PROMPT
from airport_agent.tools import build_registry
from evals.cases import Case, load_cases
from evals.cost import cost_usd
from evals.judge import (
    Calibration,
    Grade,
    calibrate,
    judge_outcome,
    load_labels,
    mean_scores,
)
from evals.metrics import Outcome, Summary, by_category, summarize
from evals.oracle import Oracle

REPO = Path(__file__).resolve().parents[1]
LEDGER_PATH = Path(__file__).parent / "ledger.jsonl"
DEFAULT_PROMPT = "default"
MODE = "recorded data"  # the data is always the committed snapshots, never a live fetch


@dataclass(frozen=True)
class ClientSpec:
    make: Callable[[Sequence[Case], Settings], LlmClient]
    is_model_backed: bool  # False means it ignores the system prompt, so prompts cannot be compared


CLIENTS: dict[str, ClientSpec] = {
    "oracle": ClientSpec(lambda cases, settings: Oracle(cases), is_model_backed=False),
    "anthropic": ClientSpec(lambda cases, settings: AnthropicClient.from_settings(settings), True),
}


class CountingClient:
    """Counts model calls, so cost per question is measured, not guessed."""

    def __init__(self, inner: LlmClient) -> None:
        self._inner = inner
        self.calls = 0

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: list[dict[str, Any]],
        allow_tools: bool,
    ) -> Message:
        self.calls += 1

        return self._inner.complete(
            system=system, messages=messages, tools=tools, allow_tools=allow_tools
        )


@dataclass(frozen=True)
class Run:
    client: str
    is_model_backed: bool
    prompt: str
    prompt_hash: str
    sha: str
    summary: Summary
    thresholds: dict[str, float]
    turns_by_category: dict[str, int]
    usage: Usage | None = None  # None for the oracle, which uses no model
    judged: tuple[Grade, ...] = ()
    judge_usage: Usage | None = None
    unjudged: int = 0  # answers the judge failed to grade


def offline_dataset(settings: Settings) -> Dataset:
    """Every request fails, so loading falls back to the committed snapshots."""
    transport = httpx.MockTransport(lambda request: httpx.Response(503))
    fetcher = HttpFetcher(httpx.Client(transport=transport), retries=0, sleep=lambda _: None)

    return load_dataset(settings, SnapshotStore(REPO / settings.snapshot_dir), fetcher)


def run_cases(cases: list[Case], client: LlmClient, data: Dataset, prompt: str) -> list[Outcome]:
    outcomes = []

    for case in cases:
        counter = CountingClient(client)
        agent = build_agent(counter, data, system_prompt=prompt)

        for turn in case.turns:
            before = counter.calls
            start = time.perf_counter()
            answer = agent.ask(turn.question)
            seconds = time.perf_counter() - start

            outcomes.append(Outcome(case, turn, answer, counter.calls - before, seconds))

    return outcomes


def judge_outcomes(
    outcomes: list[Outcome], client: AnthropicClient
) -> tuple[tuple[Grade, ...], int]:
    """Grades the model's own prose; fallback text is written by code, so it is not judged."""
    answered = [o for o in outcomes if o.answer.status is Status.ANSWERED]
    grades = [judge_outcome(client, o) for o in answered]

    return tuple(g for g in grades if g), grades.count(None)


def exit_code(run: Run) -> int:
    """1 when a metric is under its threshold or any turn got no reply from the model."""
    return 1 if failed_metrics(run) or run.summary.unavailable else 0


def failed_metrics(run: Run) -> list[str]:
    return [name for name, minimum in run.thresholds.items() if run.summary.scores[name] < minimum]


def _git(*args: str) -> str | None:
    try:
        done = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=False)
    except OSError:
        return None

    return done.stdout.strip() if done.returncode == 0 else None


def git_sha() -> str:
    """The commit, with `-dirty` when the tracked files differ from it, so a row is reproducible."""
    sha = _git("rev-parse", "--short", "HEAD")

    if sha is None:
        return "unknown"

    ledger = f":!{LEDGER_PATH.relative_to(REPO).as_posix()}"  # a run's own row is not a change
    changed = _git("status", "--porcelain", "--untracked-files=no", "--", ".", ledger)

    return f"{sha}-dirty" if changed else sha


def _judge_lines(run: Run) -> list[str]:
    if not run.judged and not run.unjudged:
        return ["Judge: not run (add --judge; scores would be advisory until --calibrate passes)"]

    means = ", ".join(f"{c} {v:.2f}" for c, v in mean_scores(run.judged).items())

    return [
        f"Judge (advisory, 1 to 5, {len(run.judged)} answers graded, {run.unjudged} not): "
        f"{means or 'no grades'}"
    ]


def _cost(usage: Usage | None, model: str, settings: Settings) -> float | None:
    return None if usage is None else cost_usd(usage, settings.model_prices[model])


def _total_cost(run: Run, settings: Settings) -> float | None:
    agent = _cost(run.usage, settings.router_model, settings)

    if agent is None:
        return None

    return agent + (_cost(run.judge_usage, settings.judge_model, settings) or 0.0)


def _cost_lines(run: Run, settings: Settings) -> list[str]:
    agent = _cost(run.usage, settings.router_model, settings)

    if agent is None or run.usage is None:
        return ["Cost: none (no model was called)"]

    questions = max(run.summary.turns, 1)
    judge = _cost(run.judge_usage, settings.judge_model, settings) or 0.0

    return [
        f"Cost: agent ${agent:.4f} ({run.usage.input_tokens} in, {run.usage.output_tokens} out "
        f"tokens, ${agent / questions:.5f} per question), judge ${judge:.4f}"
    ]


def format_report(run: Run, settings: Settings) -> str:
    lines = [
        f"EVAL RUN  {date.today()}  sha {run.sha}  models: router={settings.router_model} "
        f"judge={settings.judge_model}  mode: {MODE}  client: {run.client}  prompt: {run.prompt}",
        "Stages evaluated: data, scoring, routing, answers (deterministic checks)",
        *_judge_lines(run),
        *_cost_lines(run, settings),
        "",
        "| Metric | Score | Threshold | Pass |",
    ]

    for name, score in run.summary.scores.items():
        minimum = run.thresholds.get(name)
        verdict = "-" if minimum is None else ("yes" if score >= minimum else "NO")
        lines.append(
            f"| {name} | {score:.2f} | {minimum if minimum is not None else '-'} | {verdict} |"
        )

    if run.summary.unavailable:
        lines += [
            "",
            f"INVALID: {run.summary.unavailable} turn(s) got no reply from the model, "
            "so these scores say nothing about it.",
        ]

    lines += ["", "Failures:", *(f"- {failure}" for failure in run.summary.failures or ["none"])]
    lines += ["", "Confusion (expected -> called):"]
    lines += [f"  {a} -> {b}: {n}" for (a, b), n in sorted(run.summary.confusion.items())]

    return "\n".join(lines)


def ledger_row(run: Run, settings: Settings) -> dict[str, object]:
    return {
        "date": str(date.today()),
        "sha": run.sha,
        "mode": MODE,
        "client": run.client,
        "model_backed": run.is_model_backed,  # False: scores say nothing about a model or prompt
        "prompt": run.prompt,
        "prompt_hash": run.prompt_hash,
        "models": {"router": settings.router_model, "judge": settings.judge_model},
        "turns": run.summary.turns,
        "turns_by_category": run.turns_by_category,
        "model_calls": run.summary.model_calls,
        "seconds": round(run.summary.seconds, 2),
        "scores": {name: round(score, 4) for name, score in run.summary.scores.items()},
        "cost_usd": _total_cost(run, settings),  # agent plus judge; None: no model used
        "agent_cost_usd": _cost(run.usage, settings.router_model, settings),
        "tokens": None if run.usage is None else asdict(run.usage),
        "judge": {
            "graded": len(run.judged),
            "ungraded": run.unjudged,
            "mean_scores": {c: round(v, 2) for c, v in mean_scores(run.judged).items()},
            "cost_usd": _cost(run.judge_usage, settings.judge_model, settings),
        }
        if run.judged or run.unjudged
        else None,
    }


def append_ledger(path: Path, row: dict[str, object]) -> int:
    """Adds one line and never touches earlier ones. Returns the row number."""
    existing = path.read_text(encoding="utf-8").count("\n") if path.exists() else 0

    with path.open("a", encoding="utf-8") as ledger:
        ledger.write(json.dumps(row, sort_keys=True) + "\n")

    return existing + 1


def read_prompt(path: Path | None) -> tuple[str, str]:
    """The prompt text and the name to record for it."""
    if path is None:
        return SYSTEM_PROMPT, DEFAULT_PROMPT

    return path.read_text(encoding="utf-8"), path.stem


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.run")
    parser.add_argument("--client", choices=sorted(CLIENTS), default="oracle")
    parser.add_argument(
        "--prompt", type=Path, help="a system prompt file to try instead of the default"
    )
    parser.add_argument("--no-ledger", action="store_true", help="print the report, record nothing")
    parser.add_argument("--judge", action="store_true", help="also grade the prose (paid)")
    parser.add_argument(
        "--calibrate", action="store_true", help="only check the judge against hand labels (paid)"
    )
    args = parser.parse_args(argv)

    if args.calibrate and (args.judge or args.prompt or args.client != "oracle"):
        parser.error("--calibrate only checks the judge; use it on its own")

    if args.judge and not CLIENTS[args.client].is_model_backed:
        parser.error("the oracle's wording is written by code; there is no prose to judge")

    if args.prompt and not CLIENTS[args.client].is_model_backed:
        parser.error(f"the {args.client} client ignores the system prompt; use a model-backed one")

    return args


def build_run(args: argparse.Namespace, settings: Settings) -> Run:
    """Reads the commit first: a run that appends to the ledger makes the tree dirty."""
    sha = git_sha()
    cases = load_cases()
    prompt, name = read_prompt(args.prompt)
    data = offline_dataset(settings)
    spec = CLIENTS[args.client]
    judge_client = (  # built before the paid run, so a missing key fails before money is spent
        AnthropicClient.from_settings(settings, model=settings.judge_model) if args.judge else None
    )
    client = spec.make(cases, settings)
    outcomes = run_cases(cases, client, data, prompt)
    summary = summarize(
        outcomes, build_registry(data), settings.max_plain_count, settings.year_range
    )
    judged, unjudged = judge_outcomes(outcomes, judge_client) if judge_client else ((), 0)

    return Run(
        args.client,
        spec.is_model_backed,
        name,
        hashlib.sha256(prompt.encode()).hexdigest()[:8],
        sha,
        summary,
        settings.eval_thresholds,
        by_category(outcomes),
        client.usage if isinstance(client, AnthropicClient) else None,
        judged,
        judge_client.usage if judge_client else None,
        unjudged,
    )


def format_calibration(result: Calibration) -> str:
    verdict = "calibrated" if result.is_calibrated else "NOT calibrated"

    per_criterion = ", ".join(f"{c} {v:.2f}" for c, v in result.by_criterion.items()) or "none"

    return (
        f"Judge calibration: {result.labels} labelled answers (need {result.needed_labels}); "
        f"agreement per criterion (need {result.needed_agreement:.2f} on each): "
        f"{per_criterion}: {verdict}"
    )


def run_calibration(settings: Settings) -> int:
    labels = load_labels()
    needed, wanted = settings.judge_min_labels, settings.judge_min_agreement

    if len(labels) < needed:  # no point paying for a verdict that cannot pass
        result = Calibration(len(labels), {}, needed, wanted)
    else:
        client = AnthropicClient.from_settings(settings, model=settings.judge_model)
        result = calibrate(
            client,
            labels,
            needed_labels=needed,
            needed_agreement=wanted,
            tolerance=settings.judge_agreement_tolerance,
        )

    print(format_calibration(result))

    return 0 if result.is_calibrated else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_settings()
    load_env_file()  # the model key lives in the repo's .env; a variable already set wins

    try:
        if args.calibrate:
            return run_calibration(settings)

        run = build_run(args, settings)
    except MissingApiKeyError as error:
        print(f"Cannot run: {error}")

        return 2

    print(format_report(run, settings))

    if not args.no_ledger:
        row = append_ledger(LEDGER_PATH, ledger_row(run, settings))
        print(f"Ledger: appended row {row} to {LEDGER_PATH.relative_to(REPO)}")

    return exit_code(run)


if __name__ == "__main__":
    raise SystemExit(main())
