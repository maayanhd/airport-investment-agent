# Airport Investment Intelligence Agent

A conversational agent that helps analysts find US airports where modernization is most likely to pay off, based on passenger and flight-capacity pressure. Built as a one-day exercise; this README is the decision log, written before the code and updated as the system is built.

> Status: data foundation (PR #2) done: HTTP fetcher, snapshots, provider fallback, airport catalog. Sections marked _TBD_ are filled in as milestones land.

## 1. Scope
In scope: US airports, public aviation data, questions about congestion, long-haul share, capacity pressure and expansion candidates, with conversational follow-ups.
Out of scope: non-US airports, financial returns, forecasts we cannot support from data, and any claim about observed "unmet demand" (it is not directly measurable; we report a labelled proxy).

## 2. Assumptions (all configurable in `config.yaml`)
- Long-haul = segment distance >= 1,500 miles.
- New England = CT, MA, ME, NH, RI, VT.
- Unmet demand is a **proxy**: high load factor + delay/cancellation pressure + seat growth lagging passenger growth.
- Data vintage is shown with every answer.

### Data sources and limits
| Source | Used for | Status |
|---|---|---|
| OurAirports `airports.csv` (public domain) | airport identity, state, location, size | implemented (`data/catalog.py`) |
| BTS T-100 summary by origin airport (Socrata API, no key) | monthly departures, passengers, seats, load factor, average flight distance | implemented (`data/traffic.py`) |
| BTS On-Time Reporting (bulk monthly zips, 31 MB each) | cancellations and departure delays per airport and month | implemented (`data/delays.py`) |

How data is fetched (`data/`):
- **Live first, snapshot fallback.** Every result carries provenance: source, `live` or `snapshot`, fetch time, data month, and a note with the reason when a snapshot was used.
- **Validate before overwriting.** Live content must parse before it replaces a snapshot, so a 200 response that is an error page or a truncated file never destroys the last good copy. Snapshots are written atomically and checked against a SHA-256 on load.
- **Third-party data is untrusted.** Files are parsed into typed records; free text from a dataset is never passed to the model as instructions.
- **Coverage is explicit.** Only airports listed in `coverage_airports` are used. A covered code absent from the data is reported as "not in the data", not as "not covered".
- **A thin or cut-off response never replaces a good snapshot.** BTS data is accepted only if no query limit was reached and at least `min_airport_share` of the covered airports have `traffic_min_months` months (24, enough for last 12 vs prior 12). The BTS window is 30 months because the data lags 3 to 4 months.
- **Committed vs ignored snapshots.** The BTS snapshot is committed: it is a filtered API response (about 460 KB, only the covered airports and the columns we use), so the offline demo and CI work from a clone. The OurAirports CSV (12.7 MB, the whole world) is git-ignored and fetched on demand. Neither is a raw bulk download.
- **Inspecting data.** `airport-agent data show ANC` is read-only. `airport-agent data refresh` fetches and saves the snapshot (it exits non-zero if live data was unusable or the snapshot could not be written). `airport-agent data status` lists snapshots and flags missing files, bad checksums and stale or unknown data months.
- **On-Time data is snapshot-first.** Each monthly file is 31 MB, so it is never downloaded while serving a request. `airport-agent data refresh delays` downloads the last 12 published months, sums them per airport and month (flights, cancelled, departures 15+ minutes late, delay minutes) and saves a small snapshot that the agent reads. A 404 means "not published yet" and is skipped; any other failure keeps the old snapshot.
- **On-Time limits.** It covers only the airlines that report to BTS (large carriers), so its counts are lower than T-100 and the delay figures describe those carriers. A few covered airports have none (for example HVN and LEB), and the agent says "no delay data" for them. Delays on cancelled flights are ignored.
- **Staleness and partial data.** A snapshot older than `max_data_age_months` (6) is flagged `stale` in its provenance and the output says how old it is. Airports with fewer months than the minimum (ACK, MVY, HYA are seasonal) are listed and shown with a "do not compare years" note.
- **Known limits of `data refresh delays`.** It downloads about 370 MB one file at a time (16 minutes on a slow server), and one missing older month aborts the run and keeps the old snapshot instead of leaving a silent gap. That is acceptable for a manual command; per-month caching or parallel downloads are the next step if refreshes become frequent.

OurAirports limits: only US airports of type large, medium or small are kept (heliports, seaplane bases and closed fields are dropped). IATA codes are not unique worldwide (a small Argentine airport also has code BOS), so the country filter is required. The file is community-maintained and has no release date, so `as_of` is empty for it.

## 3. Risk and what happens when the system is wrong
Being wrong with confidence is worse than abstaining.

| Tier | Examples | Treatment |
|---|---|---|
| Low | single metric lookup (% long-haul at ANC) | answer with source and data date |
| Medium | comparisons (LA vs SNA congestion) | answer with assumptions and a "proxy" badge |
| High | rankings, recommendations, "why" narratives | answer with confidence, assumptions, full score breakdown; export requires human sign-off |

Human review does **not** sit in the chat path (it would break a conversational agent):
1. **In the request path (automatic):** every number comes from tool output; a deterministic validator blocks any number or airport not present in tool results (one retry, then abstain); out-of-scope, unknown-entity or low-confidence questions get a clarifying question or refusal.
2. **Out of band:** thumbs/flag feedback goes to a ledger; flagged answers are reviewed asynchronously; approved answers become exemplars.
3. **At the point of consequence:** anything exported or sent in the firm's name needs a human.

## 4. Deterministic vs LLM
_TBD (M3/M4)_: data, KPIs, scoring and number validation are plain Python; the LLM only chooses tools, resolves follow-ups and writes the explanation from tool output.

## 5. Scoring methodology
_TBD (M2)_

## 6. Model choice and cost
Smallest model that passes the eval for each step. Router/agent loop starts with a Haiku-class model, escalate only if evals show a gap; judge is Sonnet-class and offline only. _Measured table TBD (M6)_.

## 7. Evaluation
_TBD (M6)_: 40-question set (incl. ~8 adversarial), tool-selection precision/recall and confusion matrix, numeric checks against gold values, validator catch rate, LLM-as-judge rubric (answers the question, no invented info, tone, states assumptions) calibrated on hand-labelled samples, results appended to a ledger.

## 8. Architecture and engineering principles
KISS/YAGNI (no agent framework, vector DB or database), SOLID where it pays (small Protocols for data source, LLM client and feedback store; tool registry), DRY (one config, schemas generated from pydantic models), pure-function KPIs, enforced by ruff, mypy --strict, pytest and CI.

Tradeoffs made so far (simpler option chosen, to be revisited when a milestone needs more):
- **No Protocols yet.** There is one implementation of each data component, so `AirportCatalog` and the other Protocols are added with the first tool or second implementation that needs them (a fake LLM, a BTS source).
- **No speculative code or config.** Snapshot line counts, `persist_live` and a data-age limit were removed; each returns in the change that uses it.
- **Parse once, I/O at the edge.** `DataProvider.fetch(parse=...)` returns the parsed value, and `load_catalog` does the fetch, so `OurAirportsCatalog` is a pure lookup that tests can build from plain data.
- **Snapshot write failures are non-fatal for answers, loud for refresh.** A full or read-only disk logs a warning and the live data is still served; the cost is losing the fallback copy. The provenance carries `save_failed` and a note, so `data refresh` exits 1 with "could not write the snapshot" instead of reporting "saved" after a 16-minute download. The flag lives on `Provenance` (not on `Fetched`) because callers only keep the provenance.
- **Damaged manifest is logged, not repaired losslessly.** The next save rebuilds it and drops other entries, whose snapshots are simply refetched.
- **Single-process assumption.** One analyst or a few chat sessions in one process; concurrent writers from several processes are untested (the temp-file name is shared).
- **Read-only is a property of the provider, not of each call.** `DataProvider(store, persist=False)` is built for `show`, so `fetch` stays at 5 arguments (the ruff limit) and no caller can forget the flag. Thin or cut-off responses are rejected before any write either way.
- **Validate once, at the config boundary.** `Settings` already enforces 3-letter IATA codes, so `load_traffic` does not re-check them; a second check would guard nothing and its test needed a `Settings` that cannot exist. The data layer takes `Settings` whole to keep signatures short; if a second caller needs a different shape, pass the four values directly.
- **Unknown is stale, not a crash.** `check_age` treats an empty or malformed data month (for example a hand-edited manifest) as stale with the note "data month unknown", so the answer says so instead of ending in a traceback. Tests that load a snapshot pin `today`, because the staleness rule makes the real clock part of the result.
- **Typed lambdas over `attrgetter` for sort keys.** `attrgetter("month")` takes the field name as a string, which `mypy --strict` and renames cannot check; `lambda m: m.month` is the same length and fully typed.
- **Small helpers instead of long parsers.** `parse_traffic` reads, rejects truncation, groups, then checks coverage in four named steps (`history.py` holds the checks shared with the delays adapter). CLAUDE.md sets 20 lines as the target and about 30 as the point to split; this is a review convention, ruff only enforces complexity and argument count.

## 9. Limitations and failure cases
_TBD_: foreign-language questions, stale or missing data, small airports, weak proxies, small eval set.

Known so far: the airport catalog has no publication date (see data sources above); when live data is unavailable the answer comes from the last snapshot and must say so; the OurAirports fixture in `tests/fixtures/` is a real excerpt (dated in its README), so it proves the parser handles the real format as of that date; CI does not detect later upstream changes unless the fixture is refreshed.

## 10. At scale, monitoring and drift
_TBD_

## Development
```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy && .venv/bin/pytest
```
