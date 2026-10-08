# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/) and this project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed
- The image is rebuilt when Trentina's is, on the fleet's
  `parent-image-updated` event.

## [0.2.0] - 2026-10-08

### Added
- The image is also pushed to `ghcr.io/crunchtools/data-collector`.

### Changed
- A judge's pass is cut to the documents the key can pay for, at what that
  judge's asks have cost so far. A judge that cannot be afforded is skipped
  and the next, cheaper one is still asked.
- A run with a few unanswered asks (`incomplete`) exits 0. Only `out of
  budget`, the new `judge failed` and an error fail the unit.
- The unit may run for three hours: a run working off a backlog makes twice
  a day's calls.

## [0.1.0] - 2026-10-08

### Added
- Daily collection of Moltbook's public posts and comments, deduplicated by
  content.
- Verdicts from Trentina's shipped defense pipeline on each new document,
  three asks per judge model, each model in a process of its own.
- A SQLite database of documents, verdicts, runs and what each judge model
  cost per run.
- A budget floor read from the collector's own OpenRouter key.
- `import`, to load runs of Trentina's earlier `collect-wild` workflow.
