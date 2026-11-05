git# Judge rubric

Status: the judge runs with `python -m evals.run --client anthropic --judge`, but its scores are
advisory until `--calibrate` passes: at least `judge_min_labels` hand-labelled answers in
`evals/calibration.yaml` and `judge_min_agreement` of the judge's scores within 1 point of them.
Do not report a judge score as a result before then.

The judge reads one answer at a time with the tool results it was written from. It scores prose
only. It never produces, corrects or checks a number, a rank or a gold value: those are decided
by `evals/metrics.py`.

Score each criterion 1 to 5 and give one sentence of evidence quoted from the answer.

| Criterion | 5 means | 1 means |
|---|---|---|
| Answers the question | Directly answers what was asked, for the right airports or region | Answers a different question, or only restates the data |
| No invented information | Every claim is supported by the tool results | States a cause, trend or fact the results do not contain |
| Tone | Plain, brief, neutral; no hype, no advice to invest | Promotional, vague, or padded |
| States assumptions | Says what a figure is a proxy for, which reading of a name was used, and what was not covered | Presents a proxy as an observation |

Calibration (before the judge is trusted): label 20 or more answers by hand in
`evals/calibration.yaml`, one item each:

```yaml
- question: "How strong is BOS for expansion?"
  answer: "BOS scores 62.2 ..."          # the text the user saw
  data: "{...tool result JSON...}"       # what the answer rests on
  human: {scores: {answers_question: 5, no_invented_info: 5, tone: 4, states_assumptions: 3}}
```

Then run `python -m evals.run --calibrate`. Below the agreement threshold the judge is not used
for decisions. Only answers the model wrote are judged: a figures-only fallback is written by
code.
