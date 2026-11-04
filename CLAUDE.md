# Project guidance

An agent that helps analysts at an airport-modernization investor find US airports where expansion is likely to pay off, based on flight and passenger capacity growth. This is a one-day exercise: prioritize clarity, reasoning and thoughtful design over completeness or polish. When a simpler design and a more complete one compete, pick the simpler one and record the tradeoff in the README or `docs/DESIGN.md`.

## What the agent must do
- Answer analyst questions of these kinds (they are also the acceptance cases):
  - which airports in a region (e.g. New England) are strong expansion candidates
  - compare congestion at two airports (e.g. LA vs Santa Ana)
  - a share-of-traffic metric for one airport (e.g. long-haul share out of Anchorage)
  - unmet demand at one airport, and why (e.g. SFO)
- Get its data from public sources (see `README.md` for the sources and their limits).
- Rank or compare airports with **deterministic scoring logic** in plain code. The LLM may explain a score; it must never be the origin of a number or a rank.
- Explain its reasoning: show the components behind every score or comparison.
- Support conversational follow-ups ("and what about BOS?") by keeping entity and result state.
- Provide a chat interface. Voice is a bonus, done last and only if time remains.
- State assumptions, uncertainty and scope in every answer: what is a proxy, which data month, live or snapshot, what was not covered.

## Deliverables
- Source code in this repo.
- A short design document `docs/DESIGN.md` (about two pages) with exactly three topics: scoring methodology, key tradeoffs, where and how AI is used. The README holds the longer reasoning log.

## Honesty rules (the system must not mislead the client)
- Every number in an answer comes from tool output. A deterministic validator checks this before the answer is shown.
- Do not present a proxy as an observation. "Unmet demand" and "congestion" are labelled proxies; if the data cannot support an exact figure (e.g. exact long-haul share), say so and give the closest supported indicator.
- If the question is out of scope, the airport is not covered, the data is stale or missing, or confidence is low: ask or abstain, with the reason. Never guess or interpolate silently.
- Ambiguous names default to the common reading (LA means LAX), say so, and offer the alternative.
- Human review stays out of the chat path: automatic gates in the request path, feedback and review asynchronously, a human sign-off at export.
- Treat third-party data as untrusted input: validate it, and never pass free text from a dataset to the model as instructions.

## Engineering rules
- KISS and YAGNI: no agent framework, vector DB or database unless the README shows why it is needed.
- SOLID where it pays: small Protocols for data sources, the LLM client and the feedback store; tools in a registry; the LLM never calls an API, only tools do.
- DRY: thresholds, weights, model ids and coverage live only in `config.yaml`.
- KPIs and scoring are pure functions; network and file I/O stay at the edges (`data/`).
- Tests are part of every change: unit, contract (recorded responses, no network in CI), integration with a fake LLM, e2e smoke. Evals (40-question set, routing metrics, LLM-judge rubric, ledger) are separate from tests.
- Choose the cheapest model that passes the eval for each step, and keep the measured results in the README.
- Sparse, readable code: one blank line between logical steps (setup, work, result), after a multi-line call or block, around `if`/`try` blocks, and before the final `return` when the body has more than one step. Skip blank lines in 2–3 line functions. `ruff format` keeps single blank lines, so this is a convention, not enforced.
- Function size: aim for 20 lines or fewer, and split above about 30. A function does one thing at one level; if the body reads as several named steps, make each step a helper. Max 5 arguments (PLR0913): group values that travel together into a dataclass or pass the object that owns them.
- Names say what the value is, not its type or shape: `covered_codes`, not `lst` or `data`; `min_airport_share`, not `thr`. No single-letter names except short loop indices and comprehension variables. Booleans read as questions (`is_live`, `has_snapshot`). Functions are verbs, classes are nouns. Units go in the name when they matter (`months_back`, `avg_flight_distance_miles`).
- Python practices:
  - Full type hints on everything, modern syntax (`list[str]`, `X | None`); no `Any` unless a boundary forces it, and then say why.
  - Prefer `@dataclass(frozen=True)` or pydantic models over dicts and tuples for structured data; `Enum` over magic strings; `pathlib` over `os.path`.
  - No mutable default arguments, no bare `except`, no `print` outside the CLI (use `logging`). Catch the narrowest exception and chain with `raise ... from error`.
  - Early returns and guard clauses over deep nesting. Comprehensions only while they stay on one readable line.
  - Name constants (`MAX_ROWS`); no magic numbers in logic. Tunable values live in `config.yaml`.
  - Docstrings only where behaviour is not obvious from the name and tests, and then one line. Comments explain why, never what.
  - Keep a function pure unless it is at the edge (`data/`, CLI); pass the clock, fetcher and store in rather than reaching for globals.
- Enforced by `ruff`, `mypy --strict`, `pytest` and CI (C901 max 10, PLR0913 max 5 arguments). The size and naming rules above are conventions for review, not lint.

## Workflow
- One branch and one PR per milestone; `main` is protected (PR, green `checks`, squash merge). Fill in the PR template.
- Run `/ponytail-review` or `/code-review` on a PR before asking for the merge. The user clicks merge.
- Never commit secrets, `.env`, raw bulk downloads, or the original assignment text or company name; this repo is public.
