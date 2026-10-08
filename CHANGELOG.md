# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/) and this project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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
