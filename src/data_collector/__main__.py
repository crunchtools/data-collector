"""Command line: ``run`` once a day, ``import`` earlier results, ``check`` the image.

Settings come from the environment, as the container is given them
(``deploy/collector.env.example`` lists every one):

* ``COLLECTOR_DB``: the SQLite file. Default ``/data/collector.db``.
* ``COLLECTOR_MODELS``: judge models, comma-separated, ``vendor/model`` or
  ``vendor/model:effort`` for a reasoning model.
* ``COLLECTOR_LIMIT``: documents to collect at most. Default 300.
* ``COLLECTOR_VOTES``: asks per document and model. Default 3.
* ``COLLECTOR_CONCURRENCY``: asks in flight at once. Default 6.
* ``COLLECTOR_BUDGET_FLOOR``: dollars a model's pass must leave on the key.
  Default 0.50.
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
from .collect import SETTLED, Judge, Settings, import_run, judge_pass, run
from .feed import Moltbook
from .store import Store

EXIT_UNFINISHED = 3
# Seconds a judge call may wait out a rate limit. This is a batch job: it is
# better to wait than to record an ask as unanswered.
PATIENCE_SECONDS = 600


def _settings() -> Settings:
    models = os.environ.get("COLLECTOR_MODELS", "")
    return Settings(
        limit=int(os.environ.get("COLLECTOR_LIMIT", "300")),
        votes=int(os.environ.get("COLLECTOR_VOTES", "3")),
        concurrency=int(os.environ.get("COLLECTOR_CONCURRENCY", "6")),
        floor=float(os.environ.get("COLLECTOR_BUDGET_FLOOR", "0.50")),
        judges=tuple(Judge.parse(spec) for spec in models.split(",") if spec.strip()),
    )


def judge_environment(judge: Judge, names: dict[str, str], base: dict[str, str]) -> dict[str, str]:
    """The environment of the process that judges with ``judge``: ``base``
    with Trentina's judge settings (``pipeline.JUDGE_ENVIRONMENT``) set."""
    env = {**base, names["provider"]: "openrouter", names["model"]: judge.model}
    env[names["patience"]] = str(PATIENCE_SECONDS)
    env.pop(names["effort"], None)
    if judge.effort:
        env[names["effort"]] = judge.effort
    return env


async def _model_process(judge: Judge, *command: str) -> bool:
    """Run ``command`` of this program in a process whose Trentina
    configuration names ``judge``'s model. True when it exits 0."""
    from . import pipeline

    env = judge_environment(judge, pipeline.JUDGE_ENVIRONMENT, dict(os.environ))
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "data_collector", *command, env=env
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
        pass_for=lambda judge, run_id, most: _model_process(
            judge, "judge", "--run", str(run_id), "--most", str(most)
        ),
        clock=time.time,
    )
    print(f"run finished: {outcome}")
    return 0 if outcome in SETTLED else EXIT_UNFINISHED


async def _judge(store: Store, run_id: int, most: int) -> int:
    from . import pipeline

    model = os.environ[pipeline.JUDGE_ENVIRONMENT["model"]]
    problem = pipeline.judge_problem(model)
    if problem is not None:
        print(f"not judging: {problem}", file=sys.stderr)
        return EXIT_UNFINISHED
    finished = await judge_pass(store, run_id, model, _settings(), pipeline.judge, most)
    return 0 if finished else EXIT_UNFINISHED


def _check_judge() -> int:
    """In a judge's own process: would Trentina judge with this model?"""
    from . import pipeline

    problem = pipeline.judge_problem(os.environ[pipeline.JUDGE_ENVIRONMENT["model"]])
    print(problem or "judge configured")
    return 1 if problem else 0


async def _check() -> int:
    """Prove the image can do its job, with no real key and no model call:
    Trentina imports, the classifier loads and gives an opinion, and every
    configured judge model would be the one Trentina asks, with a prompt
    pack of its own."""
    from . import pipeline

    verdict = await pipeline.judge("The maintenance window is Tuesday at 02:00 UTC.", "check")
    print(f"trentina {pipeline.versions()}, l1 {verdict['l1_risk']}, l2 {verdict['l2_label']}")
    judges = _settings().judges
    configured = [await _model_process(judge, "check-judge") for judge in judges]
    return 0 if verdict["l2_label"] and judges and all(configured) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="data_collector", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="collect the feed and judge what is new")
    judge = commands.add_parser("judge", help="one judge model's pass of a run (internal)")
    judge.add_argument("--run", type=int, required=True)
    judge.add_argument("--most", type=int, required=True, help="documents at most")
    imported = commands.add_parser("import", help="load a collect-wild run's artifacts")
    imported.add_argument("artifacts", type=Path, help="the directory gh run download wrote")
    commands.add_parser("check", help="verify the image without a real key")
    commands.add_parser("check-judge", help="one judge's configuration (internal)")
    args = parser.parse_args(argv)
    if args.command == "check":
        return asyncio.run(_check())
    if args.command == "check-judge":
        return _check_judge()
    store = Store(os.environ.get("COLLECTOR_DB", "/data/collector.db"))
    if args.command == "run":
        return asyncio.run(_run(store))
    if args.command == "judge":
        return asyncio.run(_judge(store, args.run, args.most))
    print(f"imported as run {import_run(store, args.artifacts, time.time())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
