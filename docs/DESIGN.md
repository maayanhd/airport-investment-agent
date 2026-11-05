# Design

> Status: KPIs, scoring, the tool registry, all five tools, the answer validator, the agent loop, the model client, the API and the chat page are done. Measured model results are not. Parts marked _TBD_ get filled in as milestones land. The README holds the longer reasoning log.

## 1. Scoring methodology

**Principle.** Every number and every rank comes from pure functions over public data. The LLM can explain a score, but it never produces one.

**Explicit assumptions.** These assumptions define what the product does and does not claim:
- Expansion desirability is an operational-pressure proxy, not an estimate of financial return, construction feasibility, available land, gate capacity, airline interest, passenger willingness to pay, or a forecast of future demand.
- Growth is compared across rolling, consecutive trailing windows. The latest available month is allowed only when each source has enough complete history; missing or seasonal months are not filled in.
- Passenger growth and capacity gap are based on BTS T-100 counts. They describe reported scheduled/charter passenger activity and seats in the covered data set, not actual demand that was refused or unavailable.
- Load factor and delay pressure are treated as airport-level indicators, but BTS coverage, carrier reporting and route mix differ by airport. The score does not normalise for airline mix, airport type, weather, seasonality, runway constraints, terminal ownership, geography or time zone.
- Higher delay pressure means stronger capacity pressure only for this exercise. It is not treated as proof that expansion solves a delay problem or that investment would improve service.
- Congestion, unmet demand and expansion potential are labels for the configured proxies. They are not measurements of demand, capacity, congestion or investment value.
- Fixed score weights and bounds are modelling choices made from the current covered-airport snapshot. They must be reviewed when coverage, source methodology, BTS revisions or the 2026-11 peer population change; score stability is assumed across airports and questions, not across time.
- Regional ranking means that every covered airport in the named state or region is eligible. A score is not a recommendation, and lower confidence or missing components can affect comparability even if a score is shown.
- Public-source availability, revisions and publication lag are outside the product's control. Live and snapshot values are kept separate in answers so users can see which evidence supported a result.

**Inputs (`kpis.py`).** Four KPIs per airport, from BTS T-100 (traffic) and BTS On-Time (delays):

| KPI | Definition | Window |
|---|---|---|
| `passenger_growth` | passengers, last 12 months vs the 12 before | 24 consecutive months |
| `load_factor` | passengers / seats, in percent | last 12 months |
| `delay_rate` (scored as the component `delay_pressure`) | share of operated flights departing 15+ minutes late | last 12 months (at least 9) |
| `capacity_gap` | passenger growth minus seat growth | 24 consecutive months |

A KPI is `None` when the data can't support it (too few months, or a gap in the months). We never interpolate.

**Score.** Each KPI is scaled to 0..1 between two fixed bounds from `config.yaml` (`score_bounds`) and clipped outside them. The score is the weighted sum using `score_weights` (0.3 growth, 0.3 load factor, 0.2 delay, 0.2 capacity gap), scaled to 100. Every answer shows each component's raw value, scaled value, weight and points, not just the total.

**Why fixed bounds instead of percentile rank.**
- A score doesn't change with the question. BOS scores the same in "New England" as in "US-wide", and follow-ups like "and BOS?" depend on that.
- The bounds are the 5th to 95th percentile of the 97 covered airports on 2026-11 data, rounded, so most airports spread across the scale. Load factor has a long low tail (5th percentile 48%, median 77%, 95th 82%), hence `[50, 82]`. An earlier `[65, 85]` pushed 11 airports to zero.
- The cost is that bounds age, so they need a re-check after each data refresh (a test catches a range that squashes too many airports). Percentile rank would need no upkeep, but scores would shift whenever the peer set changed.

**Proxies, labelled as proxies.**
- _Unmet demand_ at one airport reuses load factor, delay and capacity gap. It's a proxy for demand outrunning supply, not an observation.
- _Congestion_ is delay rate plus cancellation rate. Also a proxy.
- _Long-haul share_ isn't supported exactly. T-100 summary data gives an average distance per airport, not a distribution, so the answer gives the average distance and says so. A route-level source would change that.

**Confidence and abstention.** Confidence is the total weight of the components that have data (1.0 when all four do). A missing KPI is never filled in. The remaining weights are rescaled so the score still runs to 100, and the answer lists what's missing. Below `min_confidence` (0.6) the airport gets no score, only the reason. A rescaled score isn't comparable with a full one, so `rank` puts airports with all four components first and partial ones after. Data age and live-versus-snapshot don't affect confidence; they're printed next to the score (`stale`, the snapshot note) so the reader sees them. If the whole delay source is unavailable, the output says that once, because then every airport is missing `delay_pressure`, not just one.

**Worked example (BOS, 2026-11 data, `airport-agent score BOS`).** Each row shows the raw value, its position in the fixed range from 0 to 1, the weight, and the points (scaled × weight × 100).

| Component | Value | Scaled | Weight | Points |
|---|---|---|---|---|
| passenger growth | -2.3% | 0.38 | 0.30 | 11.5 |
| load factor | 81.3% | 0.98 | 0.30 | 29.4 |
| delay pressure | 24.2% late 15+ min | 0.61 | 0.20 | 12.2 |
| capacity gap | -0.5 pts | 0.45 | 0.20 | 9.1 |
| **Score** | | | | **62.2 / 100** (confidence 100%) |

Without delay data, confidence would be 80%, the other three weights would be divided by 0.8 so the score still runs to 100, and the answer would list `delay_pressure` as missing.

## 2. Key tradeoffs

- **No agent framework, vector DB or database.** A router loop and a tool registry are enough for four question types. We'd revisit only if the README shows a need.
- **Live first, snapshot fallback.** An answer is almost always available. The price is that every answer has to say which data month it used and whether the data was live.
- **Fixed coverage list** (`coverage_airports`, 97 airports). It's explicit and testable. Small airports are out and are reported as not covered.
- **Proxies instead of unavailable measurements.** Some questions get a "closest supported indicator" answer rather than an exact one.
- **Fixed score bounds** over percentile rank (see section 1).
- **Size is shown, not scored.** Each ranked airport carries its 12-month passengers and a caveat, because folding size into the score would hide the tradeoff between growth and scale in a weight nobody could defend.
- **A small gap is called equal.** `compare_congestion` names no winner within `congestion_tie_gap`, so a 0.1-point difference isn't presented as a result.
- **A rejected answer falls back to the tools' own figures**, labelled as such, instead of showing unverified wording or nothing. The cost is a blunt answer; the benefit is that nothing unchecked reaches the user.
- **Tool failures are results, not exceptions.** The registry rejects unknown arguments instead of dropping them, and turns a crash into a typed `internal_error`, so the model never sees a traceback or claims an option that didn't exist.
- **No Protocols until a second implementation needs one.** That means less indirection now and one refactor later. The tool registry is the exception, since there will be many tools.
- **Human review stays out of the chat path.** Automatic gates sit in the request path, feedback is reviewed asynchronously, and a person signs off at export.
- **A small Python eval harness** instead of a framework such as Promptfoo. The key checks (numeric gold values, routing confusion matrix, validator catch rate) are deterministic and fit plain code, and a Node tool would add a second toolchain.

### Production-grade gaps

This is a safe demo architecture, not a production service. The following work is still required before this product is exposed to real analysts or consequential decisions:

- **Identity and access:** add authenticated users, roles, authorization, API key rotation, tenant isolation if applicable, CSRF protection for browser flows and audit trails for exported or shared answers.
- **Secrets and configuration:** validate startup configuration; use a managed secret store; define environment-specific budgets and allowed data providers; prevent secrets and prompts from entering logs or telemetry.
- **Deployment and reliability:** run multiple stateless workers, put the in-memory session store behind an external store with TTL and replication, add health/readiness checks, graceful shutdown, request deadlines, timeouts, retries with bounded backoff and circuit breakers for model/data providers.
- **Scalability:** bound concurrency and memory, rate-limit abuse and expensive model calls, set per-request and per-tenant quotas, and load-test the API, model provider and snapshot startup path.
- **Data operations:** establish an owned refresh schedule with publication calendars and alerting; version snapshots and provenance; detect source schema changes and revisions; support rollback, retention and backups; quarantine or repair bad data without silently replacing prior evidence.
- **Data quality and model governance:** run larger sampled ground-truth checks, document methodology review and approval, monitor score-bound drift, data freshness and source coverage, and add a tested change process for weights, thresholds, prompts and model versions.
- **Security:** add content security headers, strict CORS, request-size limits, dependency vulnerability scanning, prompt-injection regression tests, output safety checks, protected downloads, malware/rate controls at the edge and periodic threat review of provider data, tools and LLM-generated HTML/text.
- **Validation and deterministic safety:** broaden validation beyond current heuristic patterns with sentence-level provenance and adversarial coverage; support reference citations; make abstention and fallback behavior measurable; and keep a human sign-off for investment or export decisions.
- **Observability and operations:** add structured logs, request IDs, model/tool latency and error metrics, token/cost telemetry, redaction, dashboards, alarms and runbooks. Avoid logging full questions, answers or user identifiers without an explicit retention and privacy policy.
- **Privacy and compliance:** publish retention/deletion policies, classify stored data, protect session history, support regional residency, execute vendor terms/data-processing reviews and assess accessibility and regulatory requirements for the intended audience.
- **Resilience testing and release:** add chaos/failure injection, restore drills, canary releases and rollback; run the full unit/contract/integration/e2e suite and model evals in CI for every release.
- **Client and availability:** move from localhost-only serving to a hardened web deployment; add reconnect/error states, session recovery, accessible chat controls, provider status, maintenance mode and a documented support/incident process.

## 3. Where and how AI is used

- **The LLM does:** choose which tool to call and fill in its arguments, resolve follow-ups ("and BOS?") using conversation state, and write the explanation from tool output.
- **The LLM never does:** produce a number or a rank, or call an API. Only tools call APIs, and a tool's only way in is a validated, named call.
- **Guardrails:** a deterministic validator (`validator.py`, `evidence.py`, `claims.py`) checks every draft before it is shown, and gives the model one retry. It blocks:
  - a number, or an airport code or covered city, that no tool result or the question contains. The results are this turn's; earlier turns' results count only when this turn made no tool call, so an old number can't vouch for a new answer;
  - a number with a flipped direction (a "fell" or "-" for a value the tool gives as positive, or the reverse);
  - a number that, in a sentence about one airport, belongs only to another airport;
  - a rank ("ranks 1st", "the strongest") or an "A is more delayed than B" or "A is a stronger candidate than B" claim that contradicts what the tool decided.

  If the retry also fails and the tools did return data, the user gets the tools' own figures as plain text (status `data_only`), never a refusal that hides good data. With no usable tool result, the agent abstains.
  Known limits, not mitigated: claims in wording the patterns miss ("clearly ahead"), a number two airports share by chance, which of two airports named in one sentence a number belongs to, and a city name that is also an ordinary word (Ontario, Lebanon). A tool value given as an unsigned magnitude can make an honest "fell 3.4 points" look like a flipped direction. The evals (M6) will measure how often these occur.

  Airport names from datasets are cleaned to plain characters, but that can't stop ordinary words that read like an instruction. So tool output is passed to the model as data under a system instruction not to follow it, and the validator is the backstop. Matching is exact on the rounded values the tools return (a fraction may be quoted as a percent). Dates, years after a preposition, ordinals and plain counts up to 10 are exempt.
- **Serving:** the model sits behind a small interface and only `anthropic_client.py` knows the vendor format. A reply cut off at the token limit is an error, never a shown answer. Conversations are held in memory, one agent each, and the API has no login, so it is for one analyst on localhost (README section 8).
- **Models** (`config.yaml`): a Haiku-class router, escalated only if evals show a gap, and a Sonnet-class judge used offline only. First measured run: about $0.33 per full eval (agent $0.04, judge $0.29); thresholds not yet met (README section 6).
- **Build-time AI:** Claude Code with an eval agent and code-review skills. Details are in the README.
