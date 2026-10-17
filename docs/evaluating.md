# Evaluating

`evaluate` runs Trentina's defense pipeline, under each judge model, over the
evaluation set: the documents the labels say are worth scoring
([labelling.md](labelling.md)). Collection judges nothing; this is the only
step that spends the OpenRouter key.

The collector does not re-implement Trentina. The image is built
`FROM quay.io/crunchtools/trentina` and calls `trentina.defense.defend` on
each document, which runs:

- **L1**, the deterministic layer;
- **L2**, the local classifier that ships in the Trentina image;
- **L3**, the quarantined judge model, with the prompt pack Trentina ships
  for that model.

So a verdict here is what a Trentina gateway of that version would have
decided.

## The evaluation set

`eval_members` holds it. A document joins once and never leaves, so two
pipelines are scored on the same documents:

| `why` | What joins |
|---|---|
| `attack`, `borderline` | Every document whose final label is that. |
| `benign-hard` | Every document a human settled as benign: the text that split the labellers or tripped a screen, and so the text most likely to fool a judge. |
| `benign-sample` | Documents the labellers agreed are benign, taken once in id order (a hash, so in effect at random) up to `COLLECTOR_EVAL_BENIGN` (200). |

A judge's false alarms are reported on the two benign groups separately. 200
documents give about ±3 points at a 10% false-alarm rate: enough to rank
judges, not enough for a headline rate, and they cannot tell 0% from 2%.

## When it costs anything

A verdict is kept under the **pipeline fingerprint** it was reached under
(`runs.pipeline`): a hash of Trentina's perimeter stamp (L1's version, the L2
model, revision, threshold and window) and the source of every module a
verdict depends on (`pipeline.FINGERPRINTED`). A Trentina release that leaves
those alone has the same fingerprint and owes nothing; Trentina releases
often, and most releases change the gateway, not the pipeline. One that
changes them owes the whole set again, about 250 documents × 4 judges × 3
votes, around $3.50. A document that joins the set later costs a few asks
the next night.

The base image follows Trentina's releases on its own: Dependabot looks for
a new Trentina image daily, and a non-major bump merges once `test` and the
image's `check` pass ([deploying.md](deploying.md)). The nightly evaluation
then pulls the new image and pays for the new fingerprint, if it is one.

## Judge models and votes

`COLLECTOR_MODELS` lists the judge models, as OpenRouter names them. Each is
asked `COLLECTOR_VOTES` times per document (3 by default), because a judge
does not always give the same answer. Each ask is one row in `verdicts`,
under the model and its effort (`google/gemini-3.8-flash:minimal`): the same
model thinking longer is another judge.

Trentina reads its judge model once per process, so each model's pass runs
in a process of its own.

An ask the judge did not answer is stored as `unavailable`, with Trentina's
reason in `l3_detail` (an error class, never content), and is owed again on
the next run. Two limits keep that from running away:

- A judge that answers none of its first twelve asks in a run is not asked
  the rest: the key is refused, the model is gone, or the provider is down.
- A document a judge has left unanswered in three separate runs is left
  alone. Some can never be answered, and a judge that leaks its canary on
  one is itself a finding: look for `l3_detail` values that are not a status
  code.

Trentina reports a misconfigured judge as `unavailable`, with no error. So
each judge's process first checks that Trentina is configured for exactly
that model, has a key, and ships a prompt pack for it, and says why if not.

## Screens

Collection runs L1 and L2 on every new document, in a process given no key
and none of Trentina's judge settings, which refuses to start if Trentina has
a judge configured anyway. A screen costs nothing. It does two jobs: `report`
scores L1 and L2 against the labels on their own, and a document the
labellers agree is benign but a screen refused is sent to a human
([labelling.md](labelling.md#disagreements)).

## Budget

The collector has its own OpenRouter key with a spending limit, so that it
can never exhaust a key another service depends on.

Before each model's pass it reads the key's usage from OpenRouter, and
works out how many documents the key can pay for at what that model's asks
have cost so far, leaving `COLLECTOR_BUDGET_FLOOR` dollars on the key. The
pass covers that many, or does not start. An ask the key refuses would be
wasted, and would count against the document.

After the pass it reads the usage again. The difference is that model's
cost for the run, stored in `run_models.cost_usd`; `runs.cost_usd` is the
run's total. OpenRouter's usage figure can lag a few seconds, so a little of
one model's cost may be counted against the next; the run's total is the
number to trust.

## Outcomes

`runs.outcome` of an evaluation is the worst of what happened to any judge:

| Outcome | Meaning | Exit |
|---|---|---|
| `ok` | Every ask was answered, or none was owed. | 0 |
| `incomplete` | A judge left some asks unanswered, or no judge is configured. They are owed again on the next run. | 0 |
| `out of budget` | The key could not pay for every document a judge owed. | 3 |
| `judge failed` | A judge gave up: the key is refused, the model is gone, or the provider is down. | 3 |
| `error: <class>` | The run itself failed. | 1 |

A collection is `ok`, `incomplete` when its screen did not finish (what is
left is screened the next day), or `error: <class>`. A non-zero exit fails
the systemd unit, which is what the host's monitoring watches.

## Report

`report` prints, for the latest evaluated pipeline, each judge's attacks and
borderline documents caught and its false alarms on each benign group, the
same for L1 and L2 from the screens, and the labels' counts. Numbers only:
no document's text, id or reason.

```bash
podman run --rm --env-file /srv/data-collector.crunchtools.com/config/collector.env \
  -v /srv/data-collector.crunchtools.com/data:/data:Z \
  quay.io/crunchtools/data-collector:latest report
```
