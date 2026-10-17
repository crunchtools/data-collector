# data-collector Constitution

> **Version:** 1.1.0
> **Ratified:** 2026-10-08
> **Status:** Active
> **Inherits:** [crunchtools/constitution](https://github.com/crunchtools/constitution) v1.24.0
> **Profile:** Container Image

This file holds what is specific to data-collector. The fleet rules and the
Container Image profile apply at the inherited version and are checked
against this repo's files by `constitution.yml`. They are not restated here.

## Image Purpose

Jobs, in two images, that write to one SQLite file and serve nothing:

- **Collector** (`Containerfile`): once a day it reads a sample of a feed
  where prompt injection is likely, stores each new document and screens it
  with Trentina's L1 and L2 alone. Each night it runs Trentina's defense
  pipeline, under each supported judge model, over the labelled evaluation
  set, when the pipeline has changed since it last did.
- **Labeller** (`Containerfile.labeller`): each night Claude Code, with no
  tools, labels each document for the answer key the verdicts are scored
  against.

## Base Image

`quay.io/crunchtools/trentina`, pinned by version, not a UBI or Hummingbird
image directly. Trentina is itself built on Hummingbird and is distributed
as an image only (Security Gateway profile I): a library install would be
its pipeline with the pinned classifier model and parsers missing. Building
on its image is the one way to call the pipeline that ships. The image adds
a single package directory and installs nothing.

`src/data_collector/pipeline.py` is the only module that imports Trentina.

The labeller's image is built on `quay.io/crunchtools/josui`, pinned by
digest, for the same reason: it is the fleet's packaging of a pinned Claude
Code. It replaces Josui's entrypoint, runs as the database's owner (uid
65532), and adds the same package directory. `label.py`, `store.py` and the
prompt are all it uses, and they need the standard library only.

## Hostile Content

Document text is untrusted and is stored in the database only: never
logged, never baked into the image or committed, and never printed except by
`review`, to a person at a terminal, with control and format characters
removed. No agent with a tool in reach reads it: the labeller is Claude Code
with every tool, MCP server and settings file switched off, driven by plain
code, and its answers are kept only where they match a fixed schema. No author
name, handle or profile is collected. Collected text is not a test fixture;
an attack found there enters a corpus only rewritten by hand.

Every fetch goes through Trentina's egress guard, so a redirect cannot point
the collector at a private address.

## Spending

Evaluation uses an OpenRouter key of its own with a spending limit, never a
key another service depends on. It reads the key's usage around every judge
model's pass, records the cost, and does not start a pass with less than the
configured floor left. Verdicts are owed again only when the defense
pipeline's fingerprint changes.

Labelling runs on the Claude subscription, with an inference-only token
(`claude setup-token`), at night, and takes on a fixed number of documents
per run.

## What CI Proves

Unit tests replace the network, Trentina and Claude Code, and run with no
key. CI also runs `check` inside each built image: in the collector's it
imports Trentina, loads its classifier, screens one fixed sentence and
fingerprints the pipeline; in the labeller's it runs Claude Code as the
labeller's user on a read-only root. A Trentina base bump merges on its own
only once both pass.

## History

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-10-08 | Initial constitution |
| 1.1.0 | 2026-10-17 | Labeller image; evaluation of the labelled set only; `review` the one place text is printed (#9) |
