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

An ask the judge did not answer is stored as `unavailable`, with Trentina's
reason in `l3_detail` (an error class, never content), and is owed again on
the next run. Two limits keep that from running away:

- A judge that answers none of its first twelve asks in a run is not asked
  the rest: the key is refused, the model is gone, or the provider is down.
- A document a judge has left unanswered in three separate runs is left alone. Some
  can never be answered, and a judge that leaks its canary on one is itself
  a finding: look for `l3_detail` values that are not a status code.

A judge is asked about at most twice the run's document limit per run,
newest first. A new Trentina version owes the whole corpus again, and works
it off over several days inside the budget.

Trentina reports a misconfigured judge as `unavailable`, with no error. So
each judge's process first checks that Trentina is configured for exactly
that model, has a key, and ships a prompt pack for it, and says why if not.

## Budget

The collector has its own OpenRouter key with a spending limit, so that it
can never exhaust a key another service depends on.

Before each model's pass it reads the key's usage from OpenRouter, and
works out how many documents the key can pay for at what that model's asks
have cost so far, leaving `COLLECTOR_BUDGET_FLOOR` dollars on the key. The
pass covers that many, newest first, or does not start. An ask the key
refuses would be wasted, and would count against the document.

After the pass it reads the usage again. The difference is that model's
cost for the run, stored in `run_models.cost_usd`; `runs.cost_usd` is the
run's total. OpenRouter's usage figure can lag a few seconds, so a little of
one model's cost may be counted against the next; the run's total is the
number to trust.

Documents are collected and stored whatever the budget. What a judge could
not be asked today it is asked when there is money again.

## Outcomes

`runs.outcome` is the worst of what happened to any judge:

| Outcome | Meaning | Exit |
|---|---|---|
| `ok` | Every ask was answered. | 0 |
| `incomplete` | A judge left some asks unanswered, or no judge is configured. They are owed again on the next run. | 0 |
| `out of budget` | The key could not pay for every document a judge owed. | 3 |
| `judge failed` | A judge gave up: the key is refused, the model is gone, or the provider is down. | 3 |
| `error: <class>` | The run itself failed. What it had collected is kept. | 1 |

A non-zero exit fails the systemd unit, which is what the host's monitoring
watches.
