# Deploying

The image is `quay.io/crunchtools/data-collector`. It is a job, not a
server: it runs, writes to the database, and exits.

## On the host

```
/srv/data-collector.crunchtools.com/config/collector.env   settings and the key, mode 600
/srv/data-collector.crunchtools.com/data/                  collector.db, owned by uid 65532
```

Copy `deploy/collector.env.example` to `collector.env` and fill in the key.
Copy `deploy/data-collector.crunchtools.com.service` and `.timer` into
`/srv/data-collector.crunchtools.com/config/` and commit them in `/srv`
first: that copy is the one Nagios compares the running unit against, and
a unit installed without it reads as drift. Then install both from there
as regular files in `/etc/systemd/system/`. The same order applies every
time a unit changes:

```bash
cd /srv/data-collector.crunchtools.com/config
cp data-collector.crunchtools.com.service data-collector.crunchtools.com.timer /etc/systemd/system/
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
