"""Command line: ``run`` and ``evaluate`` daily, ``review`` and ``report`` by
hand, ``import`` earlier results, ``check`` the image.

Settings come from the environment, as the container is given them
(``deploy/collector.env.example`` lists every one):

* ``COLLECTOR_DB``: the SQLite file. Default ``/data/collector.db``.
* ``COLLECTOR_MODELS``: judge models for ``evaluate``, comma-separated,
  ``vendor/model`` or ``vendor/model:effort`` for a reasoning model.
* ``COLLECTOR_LIMIT``: documents to collect at most. Default 300.
* ``COLLECTOR_VOTES``: asks per document and model. Default 3.
* ``COLLECTOR_CONCURRENCY``: asks in flight at once. Default 6.
* ``COLLECTOR_BUDGET_FLOOR``: dollars a model's pass must leave on the key.
  Default 0.50.
* ``COLLECTOR_LABELLERS``: the labeller models whose agreement makes a label
  (``label.LABELLERS`` by default); ``review`` and ``report`` read it.
* ``OPENROUTER_API_KEY``: the collector's own key, for ``evaluate``.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

from .budget import budget
from .collect import SETTLED, Judge, Settings, evaluate, import_run, judge_pass, run, screen_pass
from .feed import Moltbook
from .report import report
from .review import review
from .store import Store

EXIT_UNFINISHED = 3
# Seconds a judge call may wait out a rate limit. This is a batch job: it is
# better to wait than to record an ask as unanswered.
PATIENCE_SECONDS = 600
# Trentina's settings that would let a screen's process reach a judge.
JUDGE_KEYS = ("OPENROUTER_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")


def _settings() -> Settings:
    models = os.environ.get("COLLECTOR_MODELS", "")
    return Settings(
        limit=int(os.environ.get("COLLECTOR_LIMIT", "300")),
        votes=int(os.environ.get("COLLECTOR_VOTES", "3")),
        concurrency=int(os.environ.get("COLLECTOR_CONCURRENCY", "6")),
        floor=float(os.environ.get("COLLECTOR_BUDGET_FLOOR", "0.50")),
        judges=tuple(Judge.parse(spec) for spec in models.split(",") if spec.strip()),
    )


def _labellers() -> list[str]:
    from .label import LABELLERS

    names = os.environ.get("COLLECTOR_LABELLERS", LABELLERS)
    return [name.strip() for name in names.split(",") if name.strip()]


def judge_environment(judge: Judge, names: dict[str, str], base: dict[str, str]) -> dict[str, str]:
    """The environment of the process that judges with ``judge``: ``base``
    with Trentina's judge settings (``pipeline.JUDGE_ENVIRONMENT``) set."""
    env = {**base, names["provider"]: "openrouter", names["model"]: judge.model}
    env[names["patience"]] = str(PATIENCE_SECONDS)
    env.pop(names["effort"], None)
    if judge.effort:
        env[names["effort"]] = judge.effort
    return env


def screen_environment(names: dict[str, str], base: dict[str, str]) -> dict[str, str]:
    """The environment of the process that screens: ``base`` with every key
    and judge setting taken out, so Trentina has no judge to ask."""
    dropped = {*JUDGE_KEYS, *names.values()}
    return {name: value for name, value in base.items() if name not in dropped}


async def _process(env: dict[str, str], *command: str) -> bool:
    """Run ``command`` of this program in a process with ``env``. True when
    it exits 0."""
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "data_collector", *command, env=env
    )
    return await process.wait() == 0


async def _model_process(judge: Judge, *command: str) -> bool:
    """Run ``command`` in a process whose Trentina configuration names
    ``judge``'s model."""
    from . import pipeline

    env = judge_environment(judge, pipeline.JUDGE_ENVIRONMENT, dict(os.environ))
    return await _process(env, *command)


async def _run(store: Store) -> int:
    from . import pipeline

    env = screen_environment(pipeline.JUDGE_ENVIRONMENT, dict(os.environ))
    outcome = await run(
        store,
        Moltbook(pipeline.fetch),
        _settings(),
        versions=pipeline.versions(),
        pipeline=pipeline.fingerprint(),
        screen_for=lambda most: _process(env, "screen", "--max-documents", str(most)),
        clock=time.time,
    )
    print(f"run finished: {outcome}")
    return 0 if outcome in SETTLED else EXIT_UNFINISHED


async def _screen(store: Store, max_documents: int) -> int:
    from . import pipeline

    problem = pipeline.screen_problem()
    if problem is not None:
        print(f"not screening: {problem}", file=sys.stderr)
        return EXIT_UNFINISHED
    screened = await screen_pass(
        store, pipeline.fingerprint(), pipeline.screen, max_documents, _settings().concurrency
    )
    print(f"screened {screened}")
    return 0


async def _evaluate(store: Store) -> int:
    from . import pipeline

    key = os.environ["OPENROUTER_API_KEY"]
    outcome = await evaluate(
        store,
        _settings(),
        versions=pipeline.versions(),
        pipeline=pipeline.fingerprint(),
        spent=lambda: budget(key),
        pass_for=lambda judge, run_id, max_documents: _model_process(
            judge, "judge", "--run", str(run_id), "--max-documents", str(max_documents)
        ),
        clock=time.time,
    )
    print(f"evaluation finished: {outcome}")
    return 0 if outcome in SETTLED else EXIT_UNFINISHED


async def _judge(store: Store, run_id: int, max_documents: int) -> int:
    from . import pipeline

    names = pipeline.JUDGE_ENVIRONMENT
    model = os.environ[names["model"]]
    problem = pipeline.judge_problem(model)
    if problem is not None:
        print(f"not judging: {problem}", file=sys.stderr)
        return EXIT_UNFINISHED
    spec = Judge(model, os.environ.get(names["effort"]) or None).spec
    finished = await judge_pass(store, run_id, spec, _settings(), pipeline.judge, max_documents)
    return 0 if finished else EXIT_UNFINISHED


def _check_judge() -> int:
    """In a judge's own process: would Trentina judge with this model?"""
    from . import pipeline

    problem = pipeline.judge_problem(os.environ[pipeline.JUDGE_ENVIRONMENT["model"]])
    print(problem or "judge configured")
    return 1 if problem else 0


async def _check() -> int:
    """Prove the image can do its job, with no real key and no model call:
    Trentina imports, the classifier loads and gives an opinion, the pipeline
    has a fingerprint, and every configured judge model would be the one
    Trentina asks, with a prompt pack of its own."""
    from . import pipeline

    verdict = await pipeline.screen("The maintenance window is Tuesday at 02:00 UTC.", "check")
    print(
        f"trentina {pipeline.versions()}, pipeline {pipeline.fingerprint()}, "
        f"l1 {verdict['l1_risk']}, l2 {verdict['l2_label']}"
    )
    judges = _settings().judges
    configured = [await _model_process(judge, "check-judge") for judge in judges]
    return 0 if verdict["l2_label"] and judges and all(configured) else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="data_collector", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="collect the feed and screen what is new")
    commands.add_parser("evaluate", help="judge the evaluation set under this pipeline")
    commands.add_parser("review", help="label disputed documents by hand (a terminal only)")
    commands.add_parser("report", help="judges and layers scored against the labels")
    screen = commands.add_parser("screen", help="L1 and L2 on unscreened documents (internal)")
    screen.add_argument("--max-documents", type=int, required=True)
    judge = commands.add_parser("judge", help="one judge model's pass of a run (internal)")
    judge.add_argument("--run", type=int, required=True)
    judge.add_argument("--max-documents", type=int, required=True)
    imported = commands.add_parser("import", help="load a collect-wild run's artifacts")
    imported.add_argument("artifacts", type=Path, help="the directory gh run download wrote")
    commands.add_parser("check", help="verify the image without a real key")
    commands.add_parser("check-judge", help="one judge's configuration (internal)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "check":
        return asyncio.run(_check())
    if args.command == "check-judge":
        return _check_judge()
    store = Store(os.environ.get("COLLECTOR_DB", "/data/collector.db"))
    if args.command == "run":
        return asyncio.run(_run(store))
    if args.command == "screen":
        return asyncio.run(_screen(store, args.max_documents))
    if args.command == "evaluate":
        return asyncio.run(_evaluate(store))
    if args.command == "judge":
        return asyncio.run(_judge(store, args.run, args.max_documents))
    if args.command == "review":
        return review(store, _labellers(), sys.stdin, sys.stdout, time.time)
    if args.command == "report":
        print(report(store, _labellers()))
        return 0
    print(f"imported as run {import_run(store, args.artifacts, time.time())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
