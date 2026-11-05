# Design

The README holds the longer reasoning log, the assumptions, the limits and the evaluation. This document is the short version: how a score is built, what was traded away, and where AI is used.

## 1. Scoring methodology

**Principle.** Every number and every rank comes from pure functions over public data (BTS T-100 traffic, BTS On-Time delays, OurAirports). The LLM can explain a score but never produces one.

**Inputs (`kpis.py`).** Four KPIs per airport:

| KPI | Definition | Window |
|---|---|---|
| `passenger_growth` | passengers, last 12 months vs the 12 before | 24 consecutive months |
| `load_factor` | passengers / seats, in percent | last 12 months |
| `delay_rate` (scored as `delay_pressure`) | share of operated flights departing 15+ minutes late | last 12 months (at least 9) |
| `capacity_gap` | passenger growth minus seat growth | 24 consecutive months |

A KPI is `None` when the data cannot support it (too few months, or a gap). Nothing is interpolated.

**Score.** Each KPI is scaled to 0..1 between two fixed bounds from `config.yaml` (`score_bounds`), clipped outside them. The score is the weighted sum with `score_weights` (0.3 growth, 0.3 load factor, 0.2 delay, 0.2 capacity gap), times 100. Every answer shows each component's raw value, scaled value, weight and points.

**Why fixed bounds, not percentile rank.** A score then does not change with the question: BOS scores the same in "New England" as in "US-wide", and a follow-up like "and BOS?" depends on that. The bounds are the 5th to 95th percentile of the 97 covered airports on 2026-11 data, rounded. The cost is that bounds age, so they need a re-check after each data refresh; a test fails if too many airports sit at either end of a range.

**Proxies, labelled as proxies.** Unmet demand reuses load factor, delay and capacity gap. Congestion is the delay rate with cancellations as context. Exact long-haul share is not available from a public API, so the answer gives the average flight distance and says it is not a share.

**Confidence.** Confidence is the total weight of the components that have data. A missing KPI is never filled in: the remaining weights are rescaled, the answer names what is missing, and below `min_confidence` (0.6) there is no score, only the reason. A rescaled score is not comparable with a full one, so rankings put complete scores first.

**Worked example (BOS, `airport-agent score BOS`).**

| Component | Value | Scaled | Weight | Points |
|---|---|---|---|---|
| passenger growth | -2.3% | 0.38 | 0.30 | 11.5 |
| load factor | 81.3% | 0.98 | 0.30 | 29.4 |
| delay pressure | 24.2% late 15+ min | 0.61 | 0.20 | 12.2 |
| capacity gap | -0.5 pts | 0.45 | 0.20 | 9.1 |
| **Score** | | | | **62.2 / 100** (confidence 100%) |

## 2. Key tradeoffs

- **No agent framework, vector DB or database.** A tool-calling loop and a registry are enough for four question types.
- **Live first, snapshot fallback.** An answer is almost always available; the price is that every answer states its data month and whether it was live.
- **Fixed coverage list (97 airports).** Explicit and testable. Anything else is "not covered".
- **Proxies instead of unavailable measurements.** A "closest supported indicator" answer beats a made-up exact one.
- **Size is shown, not scored.** Ranked airports carry their 12-month passengers and a caveat. Folding size into the score would hide the growth-versus-scale tradeoff in a weight nobody could defend.
- **A small gap is called equal.** Delay rates within `congestion_tie_gap` have no winner.
- **A rejected answer falls back to the tools' own figures**, labelled as such. The answer is blunt, but nothing unchecked reaches the user.
- **Tool failures are results, not exceptions**, so the model never sees a traceback or claims an option that did not exist.
- **Human review stays out of the chat path.** Automatic gates run in the request path; feedback is reviewed asynchronously.
- **A small Python eval harness** instead of a framework: the key checks (gold figures, routing confusion, validator catch rate) are deterministic.

## 3. Where and how AI is used

- **The LLM does:** choose which tool to call and with what arguments, resolve follow-ups ("and BOS?") from conversation state, and write the explanation from tool output.
- **The LLM never does:** produce a number or a rank, or call an API. Only tools call APIs.
- **Guardrail.** A deterministic validator (`validator.py`, `evidence.py`, `claims.py`) checks every draft before it is shown and allows one rewrite. It blocks a figure or airport that no tool result or the question contains, a flipped direction ("fell" for a rise), a number that belongs to another airport, and a rank or "A is more delayed than B" claim that contradicts the tool. If the retry fails and tools did return data, the user gets those figures as plain text; with no usable data, the agent abstains. Known limits (claims in wording the patterns miss, a figure two airports share by chance) are in README section 9.
- **Untrusted data.** Airport names are cleaned to plain characters and tool output is passed to the model as data, with the validator as the backstop.
- **Models** (`config.yaml`): a Haiku-class model runs the loop, escalated only if the evals show a gap; a Sonnet-class judge grades prose offline only. Measured cost and results are in README sections 6 and 7.
- **Build-time AI:** Claude Code with code-review skills and an eval loop.
