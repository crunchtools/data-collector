# The collector calls Trentina's defense pipeline in place. Trentina is
# distributed as an image and nothing else, so this image is built on it and
# adds one package directory: no install step, and no shell needed in a base
# that has none. The path names the base image's Python; `check`, which CI
# runs inside the built image, fails if a new base moves it.
#
# Built, tested and pushed by GitHub Actions only.
FROM quay.io/crunchtools/trentina:1.1.0

LABEL maintainer="maintainer@crunchtools.com"
LABEL description="Collects documents from hostile feeds and records Trentina's verdict on each"
LABEL org.opencontainers.image.source="https://github.com/crunchtools/data-collector"
LABEL org.opencontainers.image.description="Collects documents from hostile feeds and records Trentina's verdict on each"
LABEL org.opencontainers.image.licenses="AGPL-3.0-or-later"

COPY src/data_collector/ /usr/lib/python3.14/site-packages/data_collector/

ENV COLLECTOR_DB=/data/collector.db
# One per prompt pack Trentina ships. The host's env file may narrow it.
ENV COLLECTOR_MODELS=google/gemini-2.5-flash-lite,google/gemini-3.5-flash-lite,anthropic/claude-haiku-4.5,google/gemini-3.8-flash:minimal
# Trentina's own stores are not used (`record=False`), and must not default
# to a path on a read-only root.
ENV QUARANTINE_DB=/tmp/quarantine.db
ENV FASTMCP_HOME=/tmp/fastmcp

USER 65532
ENTRYPOINT ["python", "-m", "data_collector"]
CMD ["run"]
