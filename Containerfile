# The collector calls Trentina's defense pipeline in place. Trentina is
# distributed as an image and nothing else, so this image is built on it and
# adds one package directory: no install step, and no shell needed in a base
# that has none. The path names the base image's Python; `check`, which CI
# runs inside the built image, fails if a new base moves it.
#
# Labelling runs Claude Code, whose native build is one binary that links
# only glibc: no Node, so it fits the shell-less base. A builder stage fetches
# it and checks it against the hash pinned here, in one RUN with no heredoc:
# hadolint cannot parse one, and containerfile-lint fails what it cannot read.
# The version follows Josui's pin and is bumped by hand, with the hash from
# that release's manifest.json (platforms.linux-x64.checksum).
#
# Built, tested and pushed by GitHub Actions only.
FROM quay.io/hummingbird/python:3.15-builder@sha256:b34ad63d9935a5432cb91e5816df4ad1dce19a53b82c7f6e85dd3d7c5931e183 AS claude-code
USER 0
ARG CLAUDE_CODE_VERSION=2.1.296
ARG CLAUDE_CODE_SHA256=24972e3bc859fab2b46ed4c1e51f7d6130f06d3bd550811a114640de3370d0de
# Root runs it here; the final stage COPYs it --chmod=0755 for its user.
RUN curl -fsSL --max-time 300 -o /claude \
      "https://downloads.claude.ai/claude-code-releases/${CLAUDE_CODE_VERSION}/linux-x64/claude" \
    && printf '%s  /claude\n' "$CLAUDE_CODE_SHA256" > /tmp/claude.sha256 \
    && { sha256sum -c /tmp/claude.sha256 || { rm -f /claude; exit 1; }; } \
    && chmod 0700 /claude \
    && /claude --version

FROM quay.io/crunchtools/trentina:6.25.0

LABEL maintainer="maintainer@crunchtools.com"
LABEL description="Collects documents from hostile feeds and records Trentina's verdict on each"
LABEL org.opencontainers.image.source="https://github.com/crunchtools/data-collector"
LABEL org.opencontainers.image.description="Collects documents from hostile feeds and records Trentina's verdict on each"
LABEL org.opencontainers.image.licenses="AGPL-3.0-or-later"

COPY --from=claude-code --chmod=0755 /claude /usr/bin/claude
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
