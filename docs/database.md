# Database

One SQLite file, `collector.db`, in the mounted data directory. The schema
is migrated on open (`PRAGMA user_version`, `store.MIGRATIONS`).

| Table | One row per | Columns worth knowing |
|-------|-------------|-----------------------|
| `documents` | distinct text | `id`, `source`, `kind`, `url`, `text`, `first_seen`, `last_seen`, `times_seen` |
| `runs` | run | `kind` (`collect`, `label`, `evaluate`), `pipeline`, `started_at`, `trentina_version`, `perimeter_version`, `collected`, `new_documents`, `cost_usd`, `outcome` |
| `run_models` | judge model in an evaluation | `asks`, `answered`, `cost_usd` |
| `verdicts` | ask of a judge | `run_id`, `document_id`, `model`, `flagged_by`, `l1_risk`, `l2_label`, `l2_score`, `l3_verdict`, `l3_risk`, `l3_detail` |
| `screens` | document and pipeline | `flagged_by` (`L1`, `L2` or NULL), `l1_risk`, `l2_label`, `l2_score` |
| `labels` | ask of a labeller, or a human's decision | `document_id`, `labeller`, `model`, `prompt_sha`, `label`, `reason`, `labelled_at` |
| `eval_members` | document in the evaluation set | `why` (`attack`, `borderline`, `benign-hard`, `benign-sample`), `added_at` |

`runs.pipeline` is the defense pipeline's fingerprint
([evaluating.md](evaluating.md#when-it-costs-anything)). Runs from before it
existed carry `trentina-<version>`.

`verdicts.flagged_by` is the layer that would have refused the document
(`L1`, `L2`, `L3`) or NULL when none would. `l3_verdict` is `flagged`,
`clean` or `unavailable`. `model` is the judge and its effort,
`vendor/model:effort` for a reasoning model.

`labels.label` is `attack`, `borderline`, `benign`, `unlabelable`, or
`failed` for an ask the labeller did not answer. `labeller` is the name the
label was asked of, or `human`; `model` is the model Claude Code reports
answering. `reason` is the model's own words about a hostile document:
`review` shows it to a person, and nothing else prints it. What a document
finally is (`store.Store.final_labels`) is worked out from these rows, not
stored.

Runs imported from Trentina's earlier `collect-wild` workflow carry
`trentina_version = 'benchmark'`, `pipeline = 'benchmark'` and
`outcome = 'imported'`. They asked the judge alone, so their L1 and L2
columns are NULL.

## Queries

What each evaluation cost, by model:

```sql
SELECT date(r.started_at, 'unixepoch') AS day, m.model, m.asks, m.answered,
       ROUND(m.cost_usd, 2) AS dollars
FROM run_models m JOIN runs r ON r.id = m.run_id
WHERE r.kind = 'evaluate'
ORDER BY day, m.model;
```

How far each labeller has got under the current prompt:

```sql
SELECT labeller, label, COUNT(*) FROM labels
WHERE prompt_sha = (SELECT prompt_sha FROM labels WHERE labeller != 'human'
                    ORDER BY id DESC LIMIT 1)
GROUP BY labeller, label;
```

The scores against the labels are `report`'s job
([evaluating.md](evaluating.md#report)).
