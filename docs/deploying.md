# Deploying

The image is `quay.io/crunchtools/data-collector`. It is a job, not a
server: it runs, writes to the database, and exits.

## On the host

```
/srv/data-collector.crunchtools.com/config/collector.env   settings and the key, mode 600
/srv/data-collector.crunchtools.com/data/                  collector.db, owned by uid 65532
```

Copy `deploy/collector.env.example` to `collector.env` and fill in the key.
Install `deploy/data-collector.crunchtools.com.service` and `.timer` as
regular files in `/etc/systemd/system/`, then:

```bash
systemctl daemon-reload
systemctl enable --now data-collector.crunchtools.com.timer
systemctl start data-collector.crunchtools.com.service   # a first run, by hand
```

The service logs to the journal. It exits non-zero when a run is
`out of budget`, a judge failed or the run itself did, so the unit fails
and `systemctl status` shows it ([outcomes](judging.md#outcomes)).

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
