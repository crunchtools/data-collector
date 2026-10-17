"""The runs: collect a feed and screen what is new; evaluate, which has each
judge model's pipeline answer for the evaluation set.

Everything that touches the network or Trentina is handed in, so this module
is the same code under test as on the host.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from .feed import Document
from .store import UNAVAILABLE

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from .budget import Budget
    from .store import Pending, Store

# Asks. A judge that has answered none of this many is not going to: the key
# is refused, the model is gone, or the provider is down.
GIVE_UP_AFTER = 12
IMPORTED = "imported"
OK = "ok"
INCOMPLETE = "incomplete"
OUT_OF_BUDGET = "out of budget"
JUDGE_FAILED = "judge failed"
# Worst last. A run's outcome is the worst thing that happened to any judge.
SEVERITY = (OK, INCOMPLETE, OUT_OF_BUDGET, JUDGE_FAILED)
# Outcomes that need nobody: what is unanswered is asked again tomorrow.
SETTLED = (OK, INCOMPLETE)
EFFORTS = ("minimal", "low", "medium", "high")
# Runs' worth of documents a screen takes on at once. A new pipeline owes the
# whole corpus a screen; it is worked off over days, inside the unit's timeout.
BACKLOG_FACTOR = 2


@dataclass(frozen=True)
class Judge:
    """A judge model, and how long it may think when it is a reasoning model."""

    model: str
    effort: str | None = None

    @classmethod
    def parse(cls, spec: str) -> Judge:
        """``vendor/model`` or ``vendor/model:effort``. A colon that is part
        of the model's own name (``vendor/model:free``) stays part of it."""
        model, _, effort = spec.strip().rpartition(":")
        return cls(model, effort) if model and effort in EFFORTS else cls(spec.strip())

    @property
    def spec(self) -> str:
        """The name its verdicts are kept under: the model and its effort,
        because the same model thinking longer is another judge."""
        return f"{self.model}:{self.effort}" if self.effort else self.model


@dataclass(frozen=True)
class Settings:
    """What a run collects and asks for."""

    limit: int
    votes: int
    concurrency: int
    floor: float
    judges: tuple[Judge, ...]


class Feed(Protocol):
    """A source of documents (``feed.Moltbook``)."""

    name: str

    async def documents(self, limit: int) -> list[Document]: ...


async def judge_pass(
    store: Store,
    run_id: int,
    model: str,
    settings: Settings,
    judge: Callable[[str, str], Awaitable[dict[str, Any]]],
    max_documents: int,
) -> bool:
    """Ask ``judge`` for the answers ``model`` still owes on its
    ``max_documents`` newest unanswered documents, ``concurrency`` at once.

    Runs in the model's own process. Every ask is stored, answered or not.
    Returns False when it gave up: none of the first ``GIVE_UP_AFTER`` asks
    was answered, so the rest were not made.
    """
    gate = asyncio.Semaphore(settings.concurrency)
    asked = answered = 0

    async def ask(document: Pending) -> None:
        nonlocal asked, answered
        async with gate:
            if asked >= GIVE_UP_AFTER and not answered:
                return
            verdict = await judge(document.text, document.url)
            asked += 1
            answered += verdict["l3_verdict"] != UNAVAILABLE
            store.add_verdict(run_id, document.id, model, verdict)

    owed = store.pending(run_id, model, settings.votes, max_documents)
    await asyncio.gather(*(ask(document) for document in owed for _ in range(document.owed)))
    return bool(answered) or not asked


async def screen_pass(
    store: Store,
    pipeline: str,
    screen: Callable[[str, str], Awaitable[dict[str, Any]]],
    max_documents: int,
    concurrency: int,
) -> int:
    """Record what L1 and L2 make of each of the ``max_documents`` newest
    documents with no screen under ``pipeline``. Runs in a process with no
    judge configured, so nothing is paid for. Returns how many."""
    gate = asyncio.Semaphore(concurrency)
    owed = store.unscreened(pipeline, max_documents)

    async def one(document: Pending) -> None:
        async with gate:
            store.add_screen(document.id, pipeline, await screen(document.text, document.url))

    await asyncio.gather(*(one(document) for document in owed))
    return len(owed)


async def run(
    store: Store,
    feed: Feed,
    settings: Settings,
    *,
    versions: tuple[str, str],
    pipeline: str,
    screen_for: Callable[[int], Awaitable[bool]],
    clock: Callable[[], float],
) -> str:
    """One collection run, start to finish. Returns its outcome, which is
    also stored. Nothing here is paid for.

    Args:
        store: The database.
        feed: Where documents come from.
        settings: The limit on documents.
        versions: Trentina's version and its perimeter's.
        pipeline: The defense pipeline's fingerprint.
        screen_for: Screens that many unscreened documents (``screen_pass``,
            in a process of its own with no judge on the host) and says
            whether it finished.
        clock: The time, as ``time.time`` gives it.

    Returns:
        ``ok``; ``incomplete`` when the screen did not finish (what it left
        is screened on the next run); or ``error: <class>`` when something
        raised, which is then raised again once the run is closed.
    """
    run_id = store.open_run(feed.name, versions, clock(), pipeline=pipeline)
    progress = {"collected": 0, "new_documents": 0}
    outcome = "error: interrupted"
    try:
        found = await feed.documents(settings.limit)
        progress["collected"] = len(found)
        progress["new_documents"] = store.add_documents(feed.name, found, clock())
        outcome = OK if await screen_for(settings.limit * BACKLOG_FACTOR) else INCOMPLETE
    except Exception as exc:
        outcome = f"error: {type(exc).__name__}"
        raise
    finally:
        # Whatever happened, the run row says so and keeps what was collected.
        store.close_run(
            run_id,
            clock(),
            collected=progress["collected"],
            new_documents=progress["new_documents"],
            outcome=outcome,
        )
    return outcome


async def evaluate(
    store: Store,
    settings: Settings,
    *,
    versions: tuple[str, str],
    pipeline: str,
    spent: Callable[[], Awaitable[Budget]],
    pass_for: Callable[[Judge, int, int], Awaitable[bool]],
    clock: Callable[[], float],
) -> str:
    """One evaluation run: every judge model answers for the evaluation set,
    as far as it has not under this pipeline. Returns its outcome, which is
    also stored. Under a pipeline it has already answered for, it asks nothing
    and costs nothing; a new member of the set costs a few asks.

    Args:
        store: The database.
        settings: Votes and the judge models.
        versions: Trentina's version and its perimeter's.
        pipeline: The defense pipeline's fingerprint.
        spent: Reads the key's budget; called around every model's pass.
        pass_for: Runs one judge model's pass of this run over that many
            documents and says whether it finished (``judge_pass``, in a
            process of its own on the host).
        clock: The time, as ``time.time`` gives it.

    Returns:
        ``ok``; ``incomplete`` when a judge left some asks unanswered (they
        are owed again on the next run) or no judge is configured; ``out of
        budget`` when the key could not pay for every document a judge owed,
        so that judge was asked about fewer or none; ``judge failed`` when a
        judge gave up; or ``error: <class>`` when something raised, which is
        then raised again once the run is closed. The worst of them wins.
    """
    run_id = store.open_run("eval", versions, clock(), kind="evaluate", pipeline=pipeline)
    cost_usd = 0.0
    outcome = "error: interrupted"
    try:
        outcome = OK if settings.judges else INCOMPLETE
        most = store.eval_size()
        for judge in settings.judges:
            before = await spent()
            owed = len(store.pending(run_id, judge.spec, settings.votes, most))
            # A pass is cut to what the key can pay for, at what this judge's
            # asks have cost so far: an ask the key refuses is an ask wasted.
            affordable, price = owed, store.ask_cost(judge.spec)
            if not before.covers(settings.floor):
                affordable = 0
            elif before.left is not None and price:
                affordable = min(
                    owed, int((before.left - settings.floor) / (price * settings.votes))
                )
            result = OK if affordable == owed else OUT_OF_BUDGET
            if affordable:
                finished = await pass_for(judge, run_id, affordable)
                cost = (await spent()).used - before.used
                cost_usd += cost
                asks, answered = store.close_model(run_id, judge.spec, cost)
                if not finished:
                    result = JUDGE_FAILED
                elif answered < asks:
                    result = max(result, INCOMPLETE, key=SEVERITY.index)
            outcome = max(outcome, result, key=SEVERITY.index)
    except Exception as exc:
        outcome = f"error: {type(exc).__name__}"
        raise
    finally:
        store.close_run(run_id, clock(), cost_usd=cost_usd, outcome=outcome)
    return outcome


def import_run(store: Store, artifacts: Path, now: float) -> int:
    """Load one run of Trentina's ``collect-wild`` workflow from its
    downloaded artifacts: ``wild-documents/wild.json`` and each judge's
    ``wild-judge-*/detonation.json`` under ``artifacts``. Import a run once:
    a second import records its verdicts a second time.

    Those verdicts came from the benchmark harness, which asks the judge
    alone: there is no L1 or L2 column to fill, and the run is recorded under
    the version and pipeline ``benchmark`` so it is never mistaken for the
    pipeline's own.

    Args:
        store: The database.
        artifacts: The directory ``gh run download`` wrote.
        now: When the import is made; the documents' first-seen time.

    Returns:
        The run's id.
    """
    found = json.loads((artifacts / "wild-documents" / "wild.json").read_text())
    source = found.get("source", "moltbook")
    run_id = store.open_run(source, ("benchmark", "benchmark"), now, pipeline="benchmark")
    collected = [Document(d["kind"], d["url"], d["text"]) for d in found["documents"]]
    new = store.add_documents(source, collected, now)
    known = {document.id for document in collected}
    for path in sorted(artifacts.glob("wild-judge-*/detonation.json")):
        result = json.loads(path.read_text())
        for record in (r for r in result["verdicts"] if r["document"] in known):
            for vote in record["votes"]:
                answer = UNAVAILABLE if vote is None else ("flagged" if vote else "clean")
                flagged_by = "L3" if vote else None
                store.add_verdict(
                    run_id,
                    record["document"],
                    result["model"],
                    {"l3_verdict": answer, "flagged_by": flagged_by},
                )
        store.close_model(run_id, result["model"], None)
    store.close_run(run_id, now, collected=len(collected), new_documents=new, outcome=IMPORTED)
    return run_id
