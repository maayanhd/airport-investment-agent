# Design

> Status: KPIs, config and the scoring function (with a `score` CLI command) are done; tools, the LLM layer and the validator are not. Parts marked _TBD_ are filled in as milestones land. The README holds the longer reasoning log.

## 1. Scoring methodology

**Principle.** Every number and rank comes from pure functions over public data. The LLM may explain a score; it never produces one.

**Inputs (`kpis.py`).** From BTS T-100 (traffic) and BTS On-Time (delays), per airport:

| KPI | Definition | Window |
|---|---|---|
| `passenger_growth` | passengers, last 12 months vs the 12 before | 24 consecutive months |
| `load_factor` | passengers / seats, in percent | last 12 months |
| `delay_rate` (scored as the component `delay_pressure`) | share of operated flights departing 15+ minutes late | last 12 months (min 9) |
| `capacity_gap` | passenger growth minus seat growth | 24 consecutive months |

A KPI is `None` when the data cannot support it (too few or non-consecutive months). It is never interpolated.

**Score.** Each KPI is scaled to 0..1 between two fixed bounds from `config.yaml` (`score_bounds`) and clipped outside them. The score is the weighted sum with `score_weights` (0.3 growth, 0.3 load factor, 0.2 delay, 0.2 capacity gap). Every answer shows each component's raw value, scaled value, weight and contribution, not only the total.

**Why fixed bounds, not percentile rank.**
- A score then does not change with the question: BOS scores the same in "New England" and in "US-wide", which follow-ups ("and BOS?") rely on.
- The bounds are the rounded 5th to 95th percentile of the 97 covered airports on 2026-11 data, so most airports spread across the scale. Load factor has a long low tail (5th percentile 48%, median 77%, 95th 82%), hence `[50, 82]`; an earlier `[65, 85]` clipped 11 airports to zero.
- Cost: the bounds age, so they must be re-checked when data is refreshed. Percentile rank would need no maintenance but would shift scores whenever the peer set changes.

**Proxies, labelled as such.**
- _Unmet demand_ at one airport reuses load factor, delay and capacity gap (no growth term). It is a proxy for demand outrunning supply, not an observation.
- _Congestion_ is delay rate plus cancellation rate. It is a proxy.
- _Long-haul share_: T-100 summary data gives an average distance per airport, not a distribution, so an exact share of departures at 1,500 miles or more is not supported. The answer gives average distance and says so. (Confirm against `config.yaml` `long_haul_min_miles` if a route-level source is added.)

**Confidence and abstention.** Confidence is the total weight of the components that have data (1.0 when all four do). A missing KPI is never filled in: the remaining weights are rescaled so the score still runs 0 to 100, and the answer lists what is missing. Below `min_confidence` (0.6) the airport gets no score and the reason is shown. Because a rescaled score is not comparable with a full one, `rank` puts airports with every component first, then partial ones, and every answer shows its confidence. Data age and live-versus-snapshot are not part of confidence; they are printed next to the score (`stale`, the snapshot note) so the reader sees them. If the delay source is unavailable the CLI says so once, because then every airport is missing `delay_pressure`, not just one.

**Worked example (BOS, 2026-11 data, `airport-agent score BOS`).** Each row is raw value, position in the fixed range from 0 to 1, weight, and points (scaled × weight × 100).

| Component | Value | Scaled | Weight | Points |
|---|---|---|---|---|
| passenger growth | -2.3% | 0.38 | 0.30 | 11.5 |
| load factor | 81.3% | 0.98 | 0.30 | 29.4 |
| delay pressure | 24.2% late 15+ min | 0.61 | 0.20 | 12.2 |
| capacity gap | -0.5 pts | 0.45 | 0.20 | 9.1 |
| **Score** | | | | **62.2 / 100** (confidence 100%) |

If the delay data were missing, confidence would be 80%, the other three weights would be divided by 0.8 so the score still runs to 100, and the answer would list `delay_pressure` as missing.

## 2. Key tradeoffs

- **No agent framework, vector DB or database.** A router loop and a tool registry are enough for four question types. Revisit only if the README shows a need.
- **Live first, snapshot fallback.** The answer is always available; the cost is that it must state the data month and whether it was live.
- **Fixed coverage list** (`coverage_airports`, 97 airports). Explicit and testable; small airports are out and reported as not covered.
- **Proxies instead of unavailable measurements.** Some questions get "closest supported indicator" answers.
- **Fixed score bounds** over percentile rank (see section 1).
- **No Protocols until a second implementation needs one.** Less indirection now; one refactor later.
- **Human review stays out of the chat path.** Automatic gates in the request path, feedback reviewed asynchronously, sign-off at export.
- **Small Python eval harness** rather than an eval framework such as Promptfoo: the key checks (numeric gold values, routing confusion matrix, validator catch rate) are deterministic and fit plain code; a Node tool would add a second toolchain.

## 3. Where and how AI is used

- **The LLM does:** choose which tool to call, resolve follow-ups ("and BOS?") using conversation state, and write the explanation from tool output.
- **The LLM never does:** produce a number or rank, or call an API. Only tools call APIs.
- **Guardrails:** a deterministic validator blocks any number or airport not present in tool results (one retry, then abstain). Free text from datasets is never passed to the model as instructions. _TBD: validator design._
- **Models** (`config.yaml`): a Haiku-class router, escalated only if evals show a gap; a Sonnet-class judge used offline only. _Measured results TBD (README section 6)._
- **Build-time AI:** Claude Code with an eval agent and code-review skills. Details are in the README.
