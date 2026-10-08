# The collector calls Trentina's defense pipeline in place. Trentina is
# distributed as an image and nothing else, so this image is built on it and
# adds one package directory: no install step, and no shell needed in a base
# that has none. The path names the base image's Python; `check`, which CI
# runs inside the built image, fails if a new base moves it.
#
# Built, tested and pushed by GitHub Actions only.
FROM quay.io/crunchtools/trentina:1.0.1

LABEL maintainer="maintainer@crunchtools.com"
LABEL description="Collects documents from hostile feeds and records Trentina's verdict on each"
LABEL org.opencontainers.image.source="https://github.com/crunchtools/data-collector"
LABEL org.opencontainers.image.description="Collects documents from hostile feeds and records Trentina's verdict on each"
LABEL org.opencontainers.image.licenses="AGPL-3.0-or-later"

COPY src/data_collector/ /usr/lib/python3.14/site-packages/data_collector/

ENV COLLECTOR_DB=/data/collector.db
# Trentina's own stores are not used (`record=False`), and must not default
# to a path on a read-only root.
ENV QUARANTINE_DB=/tmp/quarantine.db
ENV FASTMCP_HOME=/tmp/fastmcp

USER 65532
ENTRYPOINT ["python", "-m", "data_collector"]
CMD ["run"]
