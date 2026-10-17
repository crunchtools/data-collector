# Deploying

There are two images, both jobs rather than servers: each runs, writes to
the database, and exits.

| Image | Built from | Runs |
|---|---|---|
| `quay.io/crunchtools/data-collector` | `Containerfile`, on Trentina's image | `run` (collect and screen), `evaluate`, `review`, `report` |
| `quay.io/crunchtools/data-collector-labeller` | `Containerfile.labeller`, on Josui's image | the labeller |

## On the host

```
/srv/data-collector.crunchtools.com/config/collector.env   settings and the OpenRouter key, mode 600
/srv/data-collector.crunchtools.com/config/labeller.env    the Claude subscription's token, mode 600
/srv/data-collector.crunchtools.com/data/                  collector.db, owned by uid 65532
```

Copy `deploy/collector.env.example` to `collector.env` and fill in the key.
Copy `deploy/labeller.env.example` to `labeller.env`, and have the Claude
subscription's owner run `claude setup-token` and paste what it prints as
`CLAUDE_CODE_OAUTH_TOKEN`.

Three units and two timers:

| Unit | When | Does |
|---|---|---|
| `data-collector.crunchtools.com.service` | `.timer`, 06:17 | collects the feed and screens what is new |
| `data-collector-nightly.crunchtools.com.target` | `.timer`, 01:30 | starts the two below |
| `data-collector-label.crunchtools.com.service` | the target | labels |
| `data-collector-evaluate.crunchtools.com.service` | the target, after labelling | judges the evaluation set |

The target `Wants` both, so an evaluation still runs on the labels there are
when labelling fails. Labelling runs at night because it shares the Claude
subscription's five-hour window with whoever else uses it in the day.

Copy every file in `deploy/` except the env examples into
`/srv/data-collector.crunchtools.com/config/` and commit them in `/srv`
first: that copy is the one Nagios compares the running unit against, and a
unit installed without it reads as drift. Then install them from there as
regular files in `/etc/systemd/system/`. The same order applies every time a
unit changes:

```bash
cd /srv/data-collector.crunchtools.com/config
cp data-collector*.service data-collector*.timer data-collector*.target /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now data-collector.crunchtools.com.timer data-collector-nightly.crunchtools.com.timer
systemctl start data-collector-nightly.crunchtools.com.target   # a first night, by hand
```

The services log to the journal: outcomes and counts, never a document. A
unit exits non-zero when a run is `out of budget`, a judge or the labeller
failed, or the run itself did, so the unit fails and `systemctl status`
shows it ([outcomes](evaluating.md#outcomes)).

## Following Trentina

The collector's image is pinned to a Trentina version. Dependabot looks for
a new Trentina image daily, and `dependabot-automerge.yml` merges a
non-major bump once the "Image gates" ruleset's checks pass: `test`, and
`build-and-push-quay`, which runs `check` inside the built image and so
proves every judge still has a prompt pack. A major bump waits for a person.
The merge pushes `:latest`, which the next night's evaluation pulls.

The labeller's image is pinned to a digest of Josui's, which Dependabot
updates the same way but which nothing merges on its own.
## Loading earlier results

Runs of Trentina's `collect-wild` workflow can be loaded from their
downloaded artifacts:

```bash
gh run download <run-id> --repo crunchtools/trentina --dir ./artifacts
podman run --rm -v /srv/data-collector.crunchtools.com/data:/data:Z \
  -v ./artifacts:/in:ro,Z quay.io/crunchtools/data-collector:latest import /in
```

Import a run once: a second import records its verdicts a second time.

## Monitoring

A run that has not happened is visible as a database file that has stopped
changing. Watch its age with a Nagios file-age check; the collector adds no
timer or check of its own.
