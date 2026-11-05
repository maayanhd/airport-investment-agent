"""Run the question set through the agent and score it: `python -m evals.run --client oracle`.

Recorded mode only: the data comes from the committed snapshots and the model is the oracle, so a
run needs no network and no key. A live client plugs in through `CLIENTS` when it exists.
"""

import argparse
import hashlib
import json
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from airport_agent.agent import build_agent
from airport_agent.config import Settings, load_settings
from airport_agent.data.http import HttpFetcher
from airport_agent.data.snapshots import SnapshotStore
from airport_agent.dataset import Dataset, load_dataset
from airport_agent.llm import LlmClient, Message
from airport_agent.prompts import SYSTEM_PROMPT
from airport_agent.tools import build_registry
from evals.cases import Case, load_cases
from evals.metrics import Outcome, Summary, by_category, summarize
from evals.oracle import Oracle

REPO = Path(__file__).resolve().parents[1]
LEDGER_PATH = Path(__file__).parent / "ledger.jsonl"
DEFAULT_PROMPT = "default"
NOT_RUN = "judge rubric, judge calibration, model-cost table, live mode"
CLIENTS: dict[str, Callable[[Sequence[Case]], LlmClient]] = {"oracle": Oracle}


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
    prompt: str
    prompt_hash: str
    summary: Summary
    thresholds: dict[str, float]


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


def failed_metrics(run: Run) -> list[str]:
    return [name for name, minimum in run.thresholds.items() if run.summary.scores[name] < minimum]


def git_sha() -> str:
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return "unknown"

    return done.stdout.strip() or "unknown"


def format_report(run: Run, settings: Settings, mode: str) -> str:
    scores = run.summary.scores
    lines = [
        f"EVAL RUN  {date.today()}  sha {git_sha()}  models: router={settings.router_model} "
        f"judge={settings.judge_model}  mode: {mode}  client: {run.client}  prompt: {run.prompt}",
        "Stages evaluated: data, scoring, routing, answers (deterministic checks)",
        f"Stages TBD (not run): {NOT_RUN}",
        "",
        "| Metric | Score | Threshold | Pass |",
    ]

    for name, score in scores.items():
        minimum = run.thresholds.get(name)
        verdict = "-" if minimum is None else ("yes" if score >= minimum else "NO")
        lines.append(
            f"| {name} | {score:.2f} | {minimum if minimum is not None else '-'} | {verdict} |"
        )

    lines += ["", "Failures:", *(f"- {failure}" for failure in run.summary.failures or ["none"])]
    lines += ["", "Confusion (expected -> called):"]
    lines += [f"  {a} -> {b}: {n}" for (a, b), n in sorted(run.summary.confusion.items())]

    return "\n".join(lines)


def ledger_row(
    run: Run, settings: Settings, mode: str, turns_by_category: dict[str, int]
) -> dict[str, object]:
    return {
        "date": str(date.today()),
        "sha": git_sha(),
        "mode": mode,
        "client": run.client,
        "prompt": run.prompt,
        "prompt_hash": run.prompt_hash,
        "models": {"router": settings.router_model, "judge": settings.judge_model},
        "turns": run.summary.turns,
        "turns_by_category": turns_by_category,
        "model_calls": run.summary.model_calls,
        "seconds": round(run.summary.seconds, 2),
        "scores": {name: round(score, 4) for name, score in run.summary.scores.items()},
        "cost_usd": None,  # the oracle costs nothing; a live client fills this in
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

    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_settings()
    cases = load_cases()
    prompt, name = read_prompt(args.prompt)
    data = offline_dataset(settings)
    outcomes = run_cases(cases, CLIENTS[args.client](cases), data, prompt)
    summary = summarize(outcomes, build_registry(data))
    run = Run(
        args.client,
        name,
        hashlib.sha256(prompt.encode()).hexdigest()[:8],
        summary,
        settings.eval_thresholds,
    )

    print(format_report(run, settings, "recorded"))

    if not args.no_ledger:
        row = append_ledger(
            LEDGER_PATH,
            ledger_row(
                run, settings, "recorded", {k.value: v for k, v in by_category(outcomes).items()}
            ),
        )
        print(f"Ledger: appended row {row} to {LEDGER_PATH.relative_to(REPO)}")

    return 1 if failed_metrics(run) else 0


if __name__ == "__main__":
    raise SystemExit(main())
