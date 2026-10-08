# data-collector

Collects documents from feeds where prompt injection is likely, and records
what [Trentina](https://github.com/crunchtools/trentina)'s defense pipeline
makes of each one, in a single SQLite file.

It exists to answer two questions with real traffic instead of written test
cases: how often does an attack actually turn up, and does the pipeline catch
it under each judge model it supports?

1. **Collects.** Once a day it reads a sample of a feed. Today that is
   Moltbook's public posts and comments ([docs/collecting.md](docs/collecting.md)).
2. **Judges with the real pipeline.** The image is built on Trentina's, so each
   document goes through the shipped deterministic layer, the shipped
   classifier and the shipped judge with its prompt pack, once per judge model
   ([docs/judging.md](docs/judging.md)).
3. **Keeps everything.** Documents, every verdict, and what each run cost,
   in SQLite ([docs/database.md](docs/database.md)).
4. **Stays inside a budget.** It has its own OpenRouter key with a spending
   limit, reads what it has spent, and stops before the limit
   ([docs/judging.md](docs/judging.md#budget)).

## Quick Start

```bash
podman run --rm \
  --env-file /srv/data-collector.crunchtools.com/config/collector.env \
  -v /srv/data-collector.crunchtools.com/data:/data:Z \
  quay.io/crunchtools/data-collector:latest run
```

`deploy/` has the systemd service and timer that run this daily, and
`deploy/collector.env.example` lists every setting.

## Documentation

| Page | What it covers |
|------|----------------|
| [docs/collecting.md](docs/collecting.md) | What is sampled, what is kept of a document, and what is left out |
| [docs/judging.md](docs/judging.md) | How a verdict is reached, votes, cost and the budget floor |
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
