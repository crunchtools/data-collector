# data-collector Constitution

> **Version:** 1.0.0
> **Ratified:** 2026-10-08
> **Status:** Active
> **Inherits:** [crunchtools/constitution](https://github.com/crunchtools/constitution) v1.22.0
> **Profile:** Container Image

This file holds what is specific to data-collector. The fleet rules and the
Container Image profile apply at the inherited version and are checked
against this repo's files by `constitution.yml`. They are not restated here.

## Image Purpose

A job that runs once a day: it reads a sample of a feed where prompt
injection is likely, asks Trentina's defense pipeline for its verdict on
each new document under each supported judge model, and writes the
documents, the verdicts and the cost to one SQLite file. It serves nothing
and listens on nothing.

## Base Image

`quay.io/crunchtools/trentina`, pinned by version, not a UBI or Hummingbird
image directly. Trentina is itself built on Hummingbird and is distributed
as an image only (Security Gateway profile I): a library install would be
its pipeline with the pinned classifier model and parsers missing. Building
on its image is the one way to call the pipeline that ships. The image adds
a single package directory and installs nothing.

`src/data_collector/pipeline.py` is the only module that imports Trentina.

## Hostile Content

Document text is untrusted and is stored in the database only: never
logged, never printed, never baked into the image or committed. No author
name, handle or profile is collected. Collected text is not a test fixture;
an attack found there enters a corpus only rewritten by hand.

Every fetch goes through Trentina's egress guard, so a redirect cannot point
the collector at a private address.

## Spending

The collector uses an OpenRouter key of its own with a spending limit, never
a key another service depends on. It reads the key's usage around every
judge model's pass, records the cost, and does not start a pass with less
than the configured floor left.

## What CI Proves

Unit tests replace the network and Trentina, and run with no key. CI also
runs `check` inside the built image, which imports Trentina, loads its
classifier and judges one fixed sentence.

## History

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-10-08 | Initial constitution |
