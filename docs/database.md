# Database

One SQLite file, `collector.db`, in the mounted data directory.

| Table | One row per | Columns worth knowing |
|-------|-------------|-----------------------|
| `documents` | distinct text | `id`, `source`, `kind`, `url`, `text`, `first_seen`, `last_seen`, `times_seen` |
| `runs` | run | `started_at`, `trentina_version`, `perimeter_version`, `collected`, `new_documents`, `cost_usd`, `outcome` |
| `run_models` | judge model in a run | `asks`, `answered`, `cost_usd` |
| `verdicts` | ask | `run_id`, `document_id`, `model`, `flagged_by`, `l1_risk`, `l2_label`, `l2_score`, `l3_verdict`, `l3_risk` |

`flagged_by` is the layer that would have refused the document (`L1`, `L2`,
`L3`) or NULL when none would. `l3_verdict` is `flagged`, `clean` or
`unavailable`.

Runs imported from Trentina's earlier `collect-wild` workflow carry
`trentina_version = 'benchmark'` and `outcome = 'imported'`. They asked the
judge alone, so their L1 and L2 columns are NULL.

## Queries

Documents the judges disagree on, by majority of each model's answers:

```sql
WITH majority AS (
  SELECT document_id, model,
         SUM(l3_verdict = 'flagged') * 2 >= SUM(l3_verdict != 'unavailable') AS flagged
  FROM verdicts WHERE l3_verdict != 'unavailable'
  GROUP BY document_id, model
)
SELECT document_id,
       GROUP_CONCAT(CASE WHEN flagged THEN model END) AS flagged_by,
       GROUP_CONCAT(CASE WHEN NOT flagged THEN model END) AS cleared_by
FROM majority GROUP BY document_id
HAVING SUM(flagged) > 0 AND SUM(NOT flagged) > 0;
```

What each day cost, by model:

```sql
SELECT date(r.started_at, 'unixepoch') AS day, m.model, m.asks, m.answered,
       ROUND(m.cost_usd, 2) AS dollars
FROM run_models m JOIN runs r ON r.id = m.run_id
ORDER BY day, m.model;
```
