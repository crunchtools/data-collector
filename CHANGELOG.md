# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/) and this project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.3.0] - 2026-10-17

### Added
- Labels (#9). Each night Claude Opus 5.5 and Claude Fable 5.1 label every
  document attack, borderline or benign, run by Claude Code with no tools on
  the Claude subscription, from a second image, `data-collector-labeller`,
  built on Josui's. A label is final when they agree or a person decides.
- `review`: a person settles disputed documents at a terminal, with control
  and format characters removed from what is shown.
- Screens: L1 and L2 on every new document at collection, with no judge and
  no cost. A document the labellers agree is benign but a screen refused is
  disputed.
- `evaluate`: the judges are run over the evaluation set (attacks,
  borderline, human-settled benign and a frozen benign sample) only.
- `report`: each judge, and L1 and L2, scored against the labels. Numbers
  only.
- Nightly label and evaluate units under a target, at 01:30.

### Changed
- `run` collects and screens; it no longer judges. Verdicts are owed again
  when the defense pipeline's fingerprint changes, not on every Trentina
  release.
- A judge is kept under its model and effort (`vendor/model:effort`).
  Existing `google/gemini-3.8-flash` rows are migrated to `:minimal`.
- The database migrates itself on open (`PRAGMA user_version`).
- The image is rebuilt when Trentina's is, on the fleet's
  `parent-image-updated` event.
- A non-major bump of the Trentina base is merged on its own once `test`
  and the image's `check` pass, and Dependabot looks for one daily.

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
