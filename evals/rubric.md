# Judge rubric

The judge reads one answer at a time with the tool results it was written from. It scores prose
only. It never produces, corrects or checks a number, a rank or a gold value: those are decided
by the code in `evals/metrics.py`.

Score each criterion 1 to 5 and give one sentence of evidence quoted from the answer.

| Criterion | 5 means | 1 means |
|---|---|---|
| Answers the question | Directly answers what was asked, for the right airports or region | Answers a different question, or only restates the data |
| No invented information | Every claim is supported by the tool results | States a cause, trend or fact the results do not contain |
| Tone | Plain, brief, neutral; no hype, no advice to invest | Promotional, vague, or padded |
| States assumptions | Says what a figure is a proxy for, which reading of a name was used, and what was not covered | Presents a proxy as an observation |
