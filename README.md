# Airport Investment Intelligence Agent

A chat agent that helps analysts find US airports where modernization is likely to pay off, judged by passenger and flight-capacity pressure. It's a one-day exercise, so this README is the decision log: written before the code and updated as things get built.

> Status: the data layer (catalog, BTS traffic, On-Time delays), the KPIs and scoring, the tool layer (registry plus five tools), the answer validator, the agent loop, the offline eval harness, the real model client, the HTTP API and the chat page are done. The model-backed evals, the judge and the cost table are not (they need a key and a paid run). Sections marked _TBD_ get filled in as each milestone lands.

## 1. Scope
In scope: US airports, public aviation data, and questions about congestion, long-haul share, capacity pressure and expansion candidates, with follow-ups ("and what about BOS?").

Out of scope: airports outside the US, financial returns, forecasts the data can't support, and any claim about observed "unmet demand". Nobody measures that directly, so we report a labelled proxy instead.

## 2. Assumptions (all configurable in `config.yaml`)
- Long-haul means a segment of 1,500 miles or more.
- New England means CT, MA, ME, NH, RI and VT.
- Unmet demand is a **proxy**: a full plane, delay pressure, and seats growing slower than passengers.
- Every answer shows how old its data is.

### Data sources and limits
| Source | Used for | Status |
|---|---|---|
| OurAirports `airports.csv` (public domain) | airport identity, state, location, size | implemented (`data/catalog.py`) |
| BTS T-100 summary by origin airport (Socrata API, no key) | monthly departures, passengers, seats, load factor, average flight distance | implemented (`data/traffic.py`) |
| BTS On-Time Reporting (bulk monthly zips, 31 MB each) | cancellations and departure delays per airport and month | implemented (`data/delays.py`) |

How the data layer behaves (`data/`):
- **Live first, snapshot as the fallback.** Every result says where it came from: source, `live` or `snapshot`, when it was fetched, which data month it covers, and why a snapshot was used if one was.
- **Bad data never replaces good data.** Live content has to parse before it overwrites a snapshot, so an error page or a truncated file can't destroy the last good copy. Snapshots are written atomically and checked against a SHA-256 when loaded.
- **Thin responses are rejected too.** BTS traffic is accepted only if the query didn't hit its row limit and at least `min_airport_share` of the covered airports have 24 months (enough for the last 12 against the 12 before). The request window is 30 months because BTS data lags by 3 to 4 months. The catalog uses the same share rule.
- **Third-party data is untrusted.** Files are parsed into typed records. When an `Airport` is built, its name is cut to plain characters and 80 characters, and an empty name is rejected, so the CSV and the snapshot get identical treatment. That can't stop a name made of ordinary words that reads like an instruction ("Ignore previous instructions"). The real defense is that tool output reaches the model as data, under a system instruction never to follow it (M4), and that the validator only lets numbers and airports from tool results into an answer.
- **Coverage is explicit.** Only airports in `coverage_airports` are used. A covered code that's missing from the data is reported as "not in the data", not as "not covered".
- **All three snapshots are committed.** Each is a filtered result, not a raw download: BTS traffic (about 590 KB) and delays (about 120 KB) for the covered airports and the columns we use, and the covered US airports from OurAirports (13 KB, cut down from the 12.7 MB world file). A fresh clone works offline and CI doesn't need the network. `airport-agent data refresh traffic|delays|catalog` renews them.
- **Inspecting data.** `airport-agent data show ANC` is read-only. `data refresh` fetches and saves a snapshot, and exits non-zero if the live data was unusable or couldn't be written. `data status` lists the snapshots and flags missing files, bad checksums, and stale or unknown data months.
- **On-Time data is snapshot-first.** Each monthly file is 31 MB, so it's never downloaded while answering a question. `data refresh delays` downloads the last 12 published months, sums them per airport and month (flights, cancelled, departures 15+ minutes late, delay minutes), and saves a small snapshot for the agent to read. A 404 means "not published yet" and is skipped. Any other failure keeps the old snapshot.
- **On-Time limits.** Only airlines that report to BTS (the large carriers) are included, so counts are lower than T-100 and the delay figures describe those carriers. A few covered airports have none at all (HVN and LEB, for example), and the agent says "no delay data" for them. Delays on cancelled flights are ignored.
- **Stale and partial data.** A snapshot older than `max_data_age_months` (6) is flagged `stale` and the output says how old it is. Airports with fewer months than the minimum (ACK, MVY and HYA are seasonal) are listed with a "do not compare years" note.
- **`data refresh delays` is slow.** It downloads about 370 MB one file at a time, around 16 minutes on a slow server, and one missing older month aborts the run and keeps the old snapshot instead of leaving a silent gap. That's fine for a manual command. If refreshes become frequent, per-month caching comes before parallel downloads.

OurAirports limits: only US airports of type large, medium or small are kept (heliports, seaplane bases and closed fields are dropped). IATA codes aren't unique worldwide (a small Argentine airport also uses BOS), so the country filter is required. The file is community-maintained with no release date, so its `as_of` is the month we fetched it.

## 3. Risk and what happens when the system is wrong
Being wrong with confidence is worse than abstaining.

| Tier | Examples | Treatment |
|---|---|---|
| Low | single metric lookup (% long-haul at ANC) | answer with source and data date |
| Medium | comparisons (LA vs SNA congestion) | answer with assumptions and a "proxy" badge |
| High | rankings, recommendations, "why" narratives | answer with confidence, assumptions and the full score breakdown; export needs human sign-off |

Human review does **not** sit in the chat path, because it would break a conversation:
1. **In the request path (automatic):** every number comes from tool output. A deterministic validator blocks any number or airport that isn't in the tool results (one retry, then abstain). Out-of-scope, unknown-airport or low-confidence questions get a clarifying question or a refusal.
2. **Out of band:** thumbs and flag feedback go to a ledger. Flagged answers are reviewed later, and approved ones become exemplars.
3. **At the point of consequence:** anything exported or sent in the firm's name needs a person.

## 4. Deterministic vs LLM
Data, KPIs, scoring and name resolution are plain Python. The LLM picks a tool, fills in its arguments, resolves follow-ups and writes the explanation from tool output. It never calls an API and never produces a number.

**Tools** (`tools/`): a small registry of named tools. Each has a pydantic input model that gives both the JSON schema sent to the model and the validation of what comes back. A bad call returns an error result instead of raising. Every result has the same shape: `data`, `assumptions` (how a name was read, the scoring rules), `caveats` (proxies, partial, fallback or stale data), `sources` (provenance) and a typed `error` (ambiguous airport, not covered, insufficient data) that tells the agent to ask or abstain. Numbers in `data` are rounded once, with a `shown` string to quote, so the validator can match an answer against them exactly.

**Names** (`resolve.py`), tried in this order:
1. *Metro names and nicknames* the data cannot know ("LA", "NYC", "DC", "Bay Area") come from a short alias table in `config.yaml`. The first code is the default reading (LA means LAX) and the rest are offered.
2. *An airport code* resolves directly.
3. *A city* resolves from the OurAirports city column, so "Santa Ana" or "Chicago" need no entry. If a city has several covered airports, the one with the most passengers over the last 12 months is the default and the others are offered ("Chicago" reads as ORD, MDW offered). The default comes from the traffic data, not from a hand-picked list.
4. *A word from an official name* resolves if exactly one airport matches. Several matches come back as a question; more than a few ask for a clearer name.
5. *The same city name in two states* ("Portland": PDX or PWM) is never guessed; it comes back as a question.

Every assumed reading is stated in the answer. A test checks that each covered airport can be reached by its own city name, so a coverage change cannot silently break lookup.

**Run the chat** (needs `pip install -e ".[dev,app]"` and `ANTHROPIC_API_KEY` in the environment; the key is read from the environment only and is never logged or stored):
```bash
uvicorn airport_agent.api:app_from_environment --factory      # API on 127.0.0.1:8000
streamlit run ui/app.py                                       # chat page on localhost:8501
```
`POST /chat` takes `{"message": ..., "session_id": null}` and returns the answer, its status (`answered`, `data_only`, `abstained`, `unavailable`) and the assumptions, caveats and data sources, so the page can show them under every answer. Send the returned `session_id` back to continue a conversation ("and what about BOS?").

**Run a tool without the LLM:** `airport-agent tool airport_score '{"airport": "LA"}'` prints exactly what the model would see.

## 5. Scoring methodology
All plain Python (`kpis.py`, `scoring.py`). The LLM never computes or changes a number.

**KPIs** (per airport, over the last 12 months of each source; both data months are shown):
- *Passenger growth*: passengers in the last 12 months against the 12 before. Needs 24 consecutive months.
- *Load factor*: total passengers over total seats for the last 12 months, computed from the counts rather than from the rounded percentage the API gives.
- *Capacity gap*: passenger growth minus seat growth. Positive means demand is outgrowing supply.
- *Delay pressure*: share of operated flights that left 15+ minutes late (On-Time, reporting carriers only). Needs 9 consecutive months, so seasonal airports (ACK, MVY, HYA) get none and the output says so.
- Also computed for explanations: cancellation rate, average delay minutes, and departure-weighted average flight distance.

**Score** = 100 × sum(weight × scaled KPI) ÷ sum(weights of the KPIs that have data). Each KPI is scaled to 0..1 by a fixed range from `config.yaml` (`score_bounds`, clipped outside) and weighted by `score_weights` (0.3, 0.3, 0.2, 0.2). Every component points the same way, toward a stronger case for expansion: more growth, a fuller plane, more delays, demand outgrowing seats.

**Confidence** is the total weight of the KPIs that had data. Below `min_confidence` (0.6) there's no score, just the reason. A score built from 80% of the weight is shown with that confidence and the missing KPI named.

Why these choices:
- *Fixed ranges, not rank or min-max across the airports being compared.* A score then doesn't change when the user adds an airport or asks a follow-up, and one outlier can't squash everyone else. The ranges are roughly the 5th to 95th percentile of the 97 covered airports on 2026-11 data, rounded. They're assumptions, and they're echoed to the user. Load factor is the skewed one (5th percentile 48%, median 77%, 95th 82%), hence `[50, 82]`. An earlier `[65, 85]` pushed 11 airports to zero.
- *No ML.* There's no label ("this expansion paid off") to train on, 97 airports is very little data, and the score has to be explainable line by line.
- *Proxies are called proxies.* Delay pressure is a congestion proxy, since airline problems also cause delays. "Unmet demand" isn't observed: capacity gap and load factor are the closest signals. Average flight distance isn't a long-haul share, because the public BTS API has no route-level distances.
- *Partial scores aren't hidden, but they aren't ranked as equals.* A missing KPI means the remaining weights are rescaled, so that score isn't comparable with a full one. `rank` puts airports with all four components first, then partial ones, and confidence plus the missing KPI are always shown next to the number.
- *Try it:* `airport-agent score BOS BDL SFO` prints each component with its scaled value, weight and points. It prints in the order you typed, not ranked.

**Assumptions behind the four question types** (each is also stated in the answer):
- *Region:* a region is a configured name ("New England") or a state code. A code that is also a nickname ("LA") is read as the city, not the state; Louisiana needs "state LA".
- *Congestion comparison:* the metric is the delay rate over each airport's newest run of up to 12 consecutive months. The two airports may cover different months, so the answer flags a different length or end month. Rates that agree at the shown precision (0.01 percentage points) are called equal.
- *Share of traffic:* only average flight distance is available, not a long-haul share. The answer says so and gives the average with the long-haul threshold from `config.yaml`.
- *Unmet demand:* a signal is "strong" when its scaled value passes a threshold from `config.yaml`. If a signal has no data, the answer reads "3 of 4" and names the missing one.

## 6. Model choice and cost
Smallest model that passes the eval for each step. The router and agent loop start with a Haiku-class model and escalate only if the evals show a gap. The judge is Sonnet-class and offline only. _Measured table TBD (M6)._

## 7. Evaluation
`python -m evals.run --client oracle` runs the 40-question set in `evals/questions.yaml` (8 adversarial, 4 follow-ups with two turns each) through the real agent, tools and validator, then scores it with plain code and appends one row to `evals/ledger.jsonl`. It exits 1 if a metric is under its threshold in `config.yaml` (`eval_thresholds`). `--prompt FILE` swaps the system prompt, so two prompts are compared by running each and reading the two ledger rows (the row records the prompt name and a hash of its text). `--no-ledger` prints the report only. `--prompt` is refused for the oracle, which ignores the prompt, so two prompts can never look compared when they were not. Each ledger row has a `model_backed` flag and a `-dirty` suffix on the sha when tracked files differ from the commit. A misspelled key in `questions.yaml` is an error.

| Metric | What it checks |
|---|---|
| `routing_precision`, `routing_recall` | tool names the agent called against the `calls` a case expects, plus a confusion matrix of the first expected tool against the first called tool |
| `answered_rate` | answerable turns that ended `answered`; `data_only` or `abstained` means the validator had to step in |
| `gold_match` | the headline figure of each expected call (top airport and score, delay rates, average miles) is run from the data and must appear in the answer as a whole token (7 is not found in 17%). A case whose expected call fails or has no headline figure is a failure, never a skip |
| `abstention` | turns marked `expect: abstain` (out of scope, not covered, ambiguous, no data) where no tool succeeded, the reply states no figures, and it reads as a refusal or a question. A confident reply with no tool call does not count |
| `disclosures` | the case's `must_state` items (data month, live or snapshot, proxy label, alias reading, "not a share"), read from the tool results' provenance and the caveat constants where they exist, not from rendered note wording |
| `proxy_wording` | a heuristic, no threshold: the model's own text calls a proxy a proxy. The notes always carry the caveat, so this is the metric a prompt can move |

Not run yet, and said so in every report: the LLM judge (`evals/rubric.md` is written but nothing has been scored with it), judge calibration, the model-cost table, and live mode. Until a live client exists, the only client is the oracle, which does what each case expects and words the answer from the tool output. Its job is to prove the harness and the agent code: every score should be 1.00, and a test checks that a deliberately wrong router is caught. **An oracle row says nothing about a model or a prompt**; prompt tuning starts when the live client lands.


## 8. Architecture and engineering principles
KISS and YAGNI (no agent framework, vector DB or database). SOLID where it pays: small Protocols for the data source, LLM client and feedback store, and a tool registry. DRY: one config, schemas generated from pydantic models. KPIs are pure functions. ruff, `mypy --strict`, pytest and CI enforce it.

Tradeoffs so far. In each case we took the simpler option and will revisit it when a milestone needs more.

Data and I/O:
- **No data-source Protocols yet.** There's one implementation of each data component, so Protocols arrive with the first thing that needs a second one (a fake LLM, another source). The tool registry already has one, because tools are many.
- **No speculative code or config.** Snapshot line counts, `persist_live` and a data-age limit were removed. Each comes back in the change that uses it.
- **Parse once, I/O at the edge.** `DataProvider.fetch(parse=...)` returns the parsed value. `refresh_catalog` does the download and `load_catalog` only reads the snapshot, so `OurAirportsCatalog` is a pure lookup that tests can build from plain data.
- **Read-only belongs to the provider, not each call.** `DataProvider(store, persist=False)` is what `show` and the tools use, so `fetch` stays at 5 arguments (the ruff limit) and no caller can forget a flag.
- **Failing to save is quiet for answers and loud for refresh.** A full or read-only disk logs a warning and the live data is still served; the cost is losing the fallback copy. The provenance carries `save_failed` and a note, so `data refresh` exits 1 with "could not write the snapshot" instead of printing "saved" after a 16-minute download. The flag lives on `Provenance`, not on `Fetched`, because callers only keep the provenance.
- **A damaged manifest is logged, not repaired losslessly.** The next save rebuilds it and drops the other entries, whose snapshots are simply refetched.
- **Single process.** One analyst, or a few chat sessions in one process. Several writer processes are untested (the temp-file name is shared).
- **An unknown data month counts as stale.** `check_age` treats an empty or malformed month (a hand-edited manifest, say) as stale with the note "data month unknown", rather than crashing. Tests that load a snapshot pin `today`, because the staleness rule makes the real clock part of the result.
- **Validate once, at the config boundary.** `Settings` already enforces 3-letter IATA codes, so `load_traffic` doesn't check them again. The data layer takes `Settings` whole to keep signatures short; if a second caller needs a different shape, pass the four values directly.

Tools:
- **Tool failures are results, not exceptions.** `ToolRegistry.call` never raises. Unknown tools, unknown or misspelled arguments, invalid values and crashes inside a tool all come back as a typed `ToolResult`. Unknown arguments are rejected rather than dropped, so the model can't claim an option it never had. Validation errors read as a short "field: problem", and a crash is logged and returned as `internal_error` without the traceback.
- **Vague names ask, they don't list.** A name matching more than `max_name_candidates` (8) airports ("international" matches 73) gets "give a city, a state or a code". Aliases match ignoring punctuation and spacing ("L.A.", "l a"), and alias keys in `config.yaml` must already be in that plain form.
- **Caveats are for surprises.** The catalog and delays are snapshots by design, so they add a caveat only when stale. Traffic adds one when live data fell back to a snapshot. Every result still lists all its sources with their data month.
- **Round once, quote the rounded number.** `metric()` rounds a value and builds its `shown` text from that rounded value, so "12.3%" can never sit next to 0.1234. The validator then matches answers against `data` exactly, with no tolerance to tune. The same helper serves every tool, so rounding lives in one place.
- **The window is part of the KPI, not recomputed in each tool.** `compute_kpis` reports `delay_months`, the length of the consecutive run its delay figures use. Tools used to count raw rows, which overstated the window when an airport had a gap. Now a tool cannot disagree with the KPI it reports.
- **Compare on what the user sees.** The congestion gap and "worse airport" use the rounded rates. Two airports that both show 21.4% are called equal, not ranked on a hidden fifth decimal.
- **Name collisions fail closed.** A region such as "LA" is both an alias and a state code. Rather than guess, `rank_region` reads it as neither and returns an error that shows the explicit form ("state LA"). The cost is one extra turn for Louisiana; the alternative was a silent wrong region.
- **Say what was cut.** A ranking cut to `top_n` states how many scored airports are hidden and lists their codes, so a short list is not mistaken for the whole region. Partial-data airports that fell below the cut get their own line.
- **Count signals against the configuration.** `unmet_demand` reports both the signals it computed and the number configured, so "3 strong of 3" cannot hide a missing fourth signal. The configured count comes from `score_weights`, so it cannot drift from the score.
- **Different windows are flagged, not blocked.** Comparing airports whose delay data differs in length or end month still answers, with a caveat. Refusing would abstain on ordinary questions, since a month's lag or a gap is common; the caveat lets the analyst judge. An airport with too little delay data (under 9 months) is the case that does abstain.

Evals:
- **An oracle model, not a recorded one.** There are no real model responses to record yet, so the harness is proven with a stand-in that does what each case expects. The cost is that a prompt cannot be compared today; the benefit is that the metrics, ledger and thresholds are tested before a paid run depends on them.
- **Gold figures are computed, not written down.** A case lists the tool calls it expects, and the harness runs them against the same snapshot to get the headline figures. The question file never goes stale when data is refreshed, and nobody can edit a gold value to make a case pass.
- **`answered_rate` is how the validator shows up in the evals.** The agent only returns `answered` text that passed the validator, so grounding is 100% by construction. What varies between prompts is how often the first draft was rejected and the user got figures only or a refusal.
- **Abstaining hides the tools it called.** An `abstained` answer carries no tool results, so routing for that turn reads as "called nothing". It is rare and only lowers recall, so it is left as is.
- **Disclosures are read from the code-written notes.** That checks that the right tool ran. Whether the model also says it in words is the separate `proxy_wording` heuristic, kept without a threshold because it is a pattern match.
- **The prompt is an argument, not a config key.** `build_agent(..., system_prompt=...)` defaults to the text in `prompts.py`. Variants are plain files passed with `--prompt`, so a candidate prompt never changes what users get until someone copies it into `prompts.py`.

Chat API and page:
- **No login, localhost only.** `/chat` has no authentication or rate limit and each question can make several paid model calls. It listens on 127.0.0.1 by default. Do not start it with `--host 0.0.0.0` on a network you do not trust: anyone who can reach it can spend the API budget, or open 200 sessions and push other people's conversations out of memory. Putting it on a network needs a login, a rate limit and a request-size limit first.
- **Sessions live in memory.** One agent per conversation, forgotten after `session_ttl_minutes` idle or when `max_sessions` is exceeded (least recently used goes), and lost on restart. That is enough for one server process and breaks with several; sharing them needs a store such as Redis, which the exercise does not justify. A per-session lock runs one question at a time.
- **The dataset is loaded once at start-up** (live traffic if reachable, else the snapshot), so a server left running for weeks reports data that ages. Restart to refresh; the data month is in every answer's sources.
- **A first question that fails, or gets no reply from the model, leaves no session behind**, and the response then carries `session_id: null`. Errors cannot fill the store, and a client does not keep an id the server no longer has.
- **The page holds no data and no key.** It only calls the API. The helpers that format a reply live in the package (`airport_agent/chat_render.py`) so they are tested without Streamlit, and so the page can import them when Streamlit puts only `ui/` on the import path.
- **One model for routing and answering.** The agent loop is the router, so `router_model` in `config.yaml` is the model that picks tools and writes the answer. Splitting them is a change for when the model-backed evals show a reason to.
- **Worst-case latency is minutes.** One question can make about six model calls, each with a 30 s timeout and one retry: up to 6 minutes. The page waits as long as the server may take (`Settings.request_budget_seconds`, derived from the same settings), so it does not report an outage while the server is still working. Typical questions take a few seconds.

Code:
- **Typed lambdas over `attrgetter` for sort keys.** `attrgetter("month")` takes the field name as a string that `mypy --strict` and renames can't check. `lambda m: m.month` is the same length and fully typed.
- **Small helpers instead of long parsers.** `parse_traffic` reads, rejects truncation, groups, then checks coverage in four named steps (`history.py` holds the checks shared with the delays adapter). CLAUDE.md sets 20 lines as the target and about 30 as the point to split. That's a review convention; ruff only enforces complexity and argument count.

## 9. Limitations and failure cases
_TBD_: foreign-language questions, stale or missing data, small airports, weak proxies, a small eval set.

Known so far:
- **The score doesn't know airport size.** +17% growth at a small airport counts the same as +17% at a hub, and a short list can be full of small airports. Passenger volume should sit next to the score before this goes in front of an investor.
- **Some components saturate.** Several airports sit at the top of the range on passenger growth or capacity gap, so those rows can't separate them. A test fails if more than 15% of airports sit at either end of a range, so the bounds get re-checked after each refresh.
- **Delay pressure counts delays as demand.** That's the proxy design, and it means a congested airport scores higher.
- **The catalog has no publication date**, only a fetch month.
- **When live data is unavailable**, the answer comes from the last snapshot and says so.
- **The OurAirports fixture** in `tests/fixtures/` is a real excerpt (dated in its README). It shows the parser handles the real format as of that date, and CI won't notice later upstream changes until the fixture is refreshed.

## 10. At scale, monitoring and drift
_TBD_

## Development
```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev,app]"
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy && .venv/bin/pytest
```
