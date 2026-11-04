# Airport Investment Intelligence Agent

A conversational agent that helps analysts find US airports where modernization is most likely to pay off, based on passenger and flight-capacity pressure. Built as a one-day exercise; this README is the decision log, written before the code and updated as the system is built.

> Status: M0 scaffold. Sections marked _TBD_ are filled in as milestones land.

## 1. Scope
In scope: US airports, public aviation data, questions about congestion, long-haul share, capacity pressure and expansion candidates, with conversational follow-ups.
Out of scope: non-US airports, financial returns, forecasts we cannot support from data, and any claim about observed "unmet demand" (it is not directly measurable; we report a labelled proxy).

## 2. Assumptions (all configurable in `config.yaml`)
- Long-haul = segment distance >= 1,500 miles.
- New England = CT, MA, ME, NH, RI, VT.
- Unmet demand is a **proxy**: high load factor + delay/cancellation pressure + seat growth lagging passenger growth.
- Data vintage is shown with every answer.

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

## 9. Limitations and failure cases
_TBD_: foreign-language questions, stale or missing data, small airports, weak proxies, small eval set.

## 10. At scale, monitoring and drift
_TBD_

## Development
```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy && .venv/bin/pytest
```
