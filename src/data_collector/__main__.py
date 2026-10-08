"""Command line: ``run`` once a day, ``import`` earlier results, ``check`` the image.

Settings come from the environment, as the container is given them
(``deploy/collector.env.example`` lists every one):

* ``COLLECTOR_DB``: the SQLite file. Default ``/data/collector.db``.
* ``COLLECTOR_MODELS``: judge models, comma-separated, ``vendor/model`` or
  ``vendor/model:effort`` for a reasoning model.
* ``COLLECTOR_LIMIT``: documents to collect at most. Default 300.
* ``COLLECTOR_VOTES``: asks per document and model. Default 3.
* ``COLLECTOR_CONCURRENCY``: asks in flight at once. Default 6.
* ``COLLECTOR_BUDGET_FLOOR``: dollars that must be left on the key for a
  model's pass to start. Default 0.50.
* ``OPENROUTER_API_KEY``: the collector's own key.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

from .budget import budget
from .collect import Judge, Settings, import_run, judge_pass, run
from .feed import Moltbook
from .store import Store

EXIT_INCOMPLETE = 3


def _settings() -> Settings:
    models = os.environ.get("COLLECTOR_MODELS", "")
    return Settings(
        limit=int(os.environ.get("COLLECTOR_LIMIT", "300")),
        votes=int(os.environ.get("COLLECTOR_VOTES", "3")),
        concurrency=int(os.environ.get("COLLECTOR_CONCURRENCY", "6")),
        floor=float(os.environ.get("COLLECTOR_BUDGET_FLOOR", "0.50")),
        judges=tuple(Judge.parse(spec) for spec in models.split(",") if spec.strip()),
    )


async def _model_process(judge: Judge, run_id: int) -> bool:
    """One judge model's pass, in a process whose Trentina configuration
    names that model."""
    env = {**os.environ, "QUARANTINE_PROVIDER": "openrouter", "QUARANTINE_MODEL": judge.model}
    if judge.effort:
        env["QUARANTINE_REASONING_EFFORT"] = judge.effort
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "data_collector", "judge", "--run", str(run_id), env=env
    )
    return await process.wait() == 0


async def _run(store: Store) -> int:
    from . import pipeline

    key = os.environ["OPENROUTER_API_KEY"]
    outcome = await run(
        store,
        Moltbook(pipeline.fetch),
        _settings(),
        versions=pipeline.versions(),
        spent=lambda: budget(key),
        pass_for=_model_process,
        clock=time.time,
    )
    print(f"run finished: {outcome}")
    return 0 if outcome == "ok" else EXIT_INCOMPLETE


async def _judge(store: Store, run_id: int) -> int:
    from . import pipeline

    model = os.environ["QUARANTINE_MODEL"]
    finished = await judge_pass(store, run_id, model, _settings(), pipeline.judge)
    return 0 if finished else EXIT_INCOMPLETE


async def _check() -> int:
    """Prove the image can do its job without a key: Trentina imports, the
    classifier loads, and a document comes back with L1 and L2 opinions."""
    from . import pipeline

    verdict = await pipeline.judge("The maintenance window is Tuesday at 02:00 UTC.", "check")
    print(f"trentina {pipeline.versions()}, l1 {verdict['l1_risk']}, l2 {verdict['l2_label']}")
    return 0 if verdict["l2_label"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="data_collector", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="collect the feed and judge what is new")
    judge = commands.add_parser("judge", help="one judge model's pass of a run (internal)")
    judge.add_argument("--run", type=int, required=True)
    imported = commands.add_parser("import", help="load a collect-wild run's artifacts")
    imported.add_argument("--documents", type=Path, required=True)
    imported.add_argument("--judged", type=Path, nargs="*", default=[])
    commands.add_parser("check", help="verify the image without a key")
    args = parser.parse_args(argv)
    if args.command == "check":
        return asyncio.run(_check())
    store = Store(os.environ.get("COLLECTOR_DB", "/data/collector.db"))
    if args.command == "run":
        return asyncio.run(_run(store))
    if args.command == "judge":
        return asyncio.run(_judge(store, args.run))
    print(f"imported as run {import_run(store, args.documents, args.judged, time.time())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
