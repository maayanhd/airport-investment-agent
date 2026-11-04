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
| BTS flight and passenger data | traffic, capacity, load factor, delays | _TBD (M1)_ |

How data is fetched (`data/`):
- **Live first, snapshot fallback.** Every result carries provenance: source, `live` or `snapshot`, fetch time, data month, and a note with the reason when a snapshot was used.
- **Validate before overwriting.** Live content must parse before it replaces a snapshot, so a 200 response that is an error page or a truncated file never destroys the last good copy. Snapshots are written atomically and checked against a SHA-256 on load.
- **Third-party data is untrusted.** Files are parsed into typed records; free text from a dataset is never passed to the model as instructions.
- **Coverage is explicit.** Only airports listed in `coverage_airports` are used. A covered code absent from the data is reported as "not in the data", not as "not covered".

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
- **Snapshot write failures are non-fatal.** A full or read-only disk logs a warning and the live data is still served; the cost is losing the fallback copy.
- **Damaged manifest is logged, not repaired losslessly.** The next save rebuilds it and drops other entries, whose snapshots are simply refetched.
- **Single-process assumption.** One analyst or a few chat sessions in one process; concurrent writers from several processes are untested (the temp-file name is shared).

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
