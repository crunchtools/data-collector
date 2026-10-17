# data-collector

Collects documents from feeds where prompt injection is likely, and records
what [Trentina](https://github.com/crunchtools/trentina)'s defense pipeline
makes of each one, in a single SQLite file.

It exists to answer two questions with real traffic instead of written test
cases: how often does an attack actually turn up, and does the pipeline catch
it under each judge model it supports?

1. **Collects.** Once a day it reads a sample of a feed. Today that is
   Moltbook's public posts and comments ([docs/collecting.md](docs/collecting.md)).
   Each new document is screened by L1 and L2, at no cost.
2. **Labels.** Each night Claude Opus 5.5 and Claude Fable 5.1, run by Claude
   Code with no tools on the Claude subscription, label every document
   attack, borderline or benign. Where they disagree, a person decides
   ([docs/labelling.md](docs/labelling.md)). The labels are the answer key.
3. **Evaluates.** The image is built on Trentina's, so the shipped
   deterministic layer, classifier and judge with its prompt pack, under each
   judge model, are run over the labelled attacks and a benign sample. That
   happens again only when a Trentina release changes the pipeline, and the
   base image follows Trentina's releases on its own
   ([docs/evaluating.md](docs/evaluating.md)).
4. **Keeps everything, inside a budget.** Documents, labels, verdicts and
   costs go in SQLite ([docs/database.md](docs/database.md)). Evaluation has
   its own OpenRouter key with a spending limit, and stops before the limit.

## Quick Start

```bash
podman run --rm \
  --env-file /srv/data-collector.crunchtools.com/config/collector.env \
  -v /srv/data-collector.crunchtools.com/data:/data:Z \
  quay.io/crunchtools/data-collector:latest run
```

`deploy/` has the systemd units that collect daily and label and evaluate
nightly, started on their schedules by Hermes, and the two env examples list
every setting. `report` prints the judges' scores against the labels.

## Documentation

| Page | What it covers |
|------|----------------|
| [docs/collecting.md](docs/collecting.md) | What is sampled, what is kept of a document, and what is left out |
| [docs/labelling.md](docs/labelling.md) | The labels, how they are asked for, and how disagreements reach a person |
| [docs/evaluating.md](docs/evaluating.md) | The evaluation set, the pipeline fingerprint, votes, cost and the budget floor |
| [docs/database.md](docs/database.md) | Tables, and queries worth knowing |
| [docs/deploying.md](docs/deploying.md) | Running it on a host |

## Development

```bash
uv sync
uv run ruff check src tests
uv run mypy
uv run pytest
```

The tests replace the network and Trentina, so they need neither. The image
is built, tested and pushed by GitHub Actions only.

## License

AGPL-3.0-or-later
