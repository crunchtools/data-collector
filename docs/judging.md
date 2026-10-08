# Judging

The collector does not re-implement Trentina. The image is built
`FROM quay.io/crunchtools/trentina` and calls `trentina.defense.defend` on
each document, which runs:

- **L1**, the deterministic layer;
- **L2**, the local classifier that ships in the Trentina image;
- **L3**, the quarantined judge model, with the prompt pack Trentina ships
  for that model.

So a verdict here is what a Trentina gateway of that version would have
decided. Every run records the Trentina version and perimeter version it ran
under, and a new Trentina version judges every document again: what the
older one decided is kept beside it.

## Judge models and votes

`COLLECTOR_MODELS` lists the judge models, as OpenRouter names them. Each is
asked `COLLECTOR_VOTES` times per document (3 by default), because a judge
does not always give the same answer. Each ask is one row in `verdicts`.

Trentina reads its judge model once per process, so each model's pass runs
in a process of its own. A reasoning model takes its effort after a colon:
`google/gemini-3.8-flash:minimal`.

An ask the judge did not answer is stored as `unavailable` and is owed again
on the next run. A judge that answers none of its first twelve asks is not
asked the rest.

## Budget

The collector has its own OpenRouter key with a spending limit, so that it
can never exhaust a key another service depends on.

Before each model's pass it reads the key's usage from OpenRouter, and it
does not start the pass when less than `COLLECTOR_BUDGET_FLOOR` dollars are
left. After the pass it reads the usage again. The difference is that
model's cost for the run, stored in `run_models.cost_usd`; `runs.cost_usd`
is the run's total.

## Outcomes

`runs.outcome` is `ok`, `incomplete` (a judge left asks unanswered) or
`out of budget`. The process exits 0 only on `ok`, so the systemd unit shows
the difference.
