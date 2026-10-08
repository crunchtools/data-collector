"""A run: collect a feed, store what is new, and have each judge model's
pipeline give its verdict on whatever it has not yet answered for.

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


@dataclass(frozen=True)
class Judge:
    """A judge model, and how long it may think when it is a reasoning model."""

    model: str
    effort: str | None = None

    @classmethod
    def parse(cls, spec: str) -> Judge:
        """``vendor/model`` or ``vendor/model:effort``."""
        model, _, effort = spec.strip().partition(":")
        return cls(model, effort or None)


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
) -> bool:
    """Ask ``judge`` for every answer ``model`` still owes, ``concurrency`` at once.

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

    owed = store.pending(run_id, model, settings.votes)
    await asyncio.gather(*(ask(document) for document in owed for _ in range(document.owed)))
    return bool(answered) or not asked


async def run(
    store: Store,
    feed: Feed,
    settings: Settings,
    *,
    versions: tuple[str, str],
    spent: Callable[[], Awaitable[Budget]],
    pass_for: Callable[[Judge, int], Awaitable[bool]],
    clock: Callable[[], float],
) -> str:
    """One run, start to finish. Returns its outcome, which is also stored.

    Args:
        store: The database.
        feed: Where documents come from.
        settings: Limits, votes and the judge models.
        versions: Trentina's version and its perimeter's.
        spent: Reads the key's budget; called around every model's pass.
        pass_for: Runs one judge model's pass of this run and says whether it
            finished (``judge_pass``, in a process of its own on the host).
        clock: The time, as ``time.time`` gives it.

    Returns:
        ``ok``; ``incomplete`` when a judge left asks unanswered (they are
        owed again on the next run); or ``out of budget`` when the key had
        less than the floor left and the remaining judges were not asked.
    """
    run_id = store.open_run(feed.name, versions, clock())
    found = await feed.documents(settings.limit)
    new = store.add_documents(feed.name, found, clock())
    outcome = "ok"
    total = 0.0
    for judge in settings.judges:
        before = await spent()
        if not before.covers(settings.floor):
            outcome = "out of budget"
            break
        finished = await pass_for(judge, run_id)
        cost = (await spent()).used - before.used
        total += cost
        asks, answered = store.close_model(run_id, judge.model, cost)
        if not finished or answered < asks:
            outcome = "incomplete"
    store.close_run(
        run_id, clock(), collected=len(found), new_documents=new, cost_usd=total, outcome=outcome
    )
    return outcome


def import_run(store: Store, documents: Path, judged: list[Path], now: float) -> int:
    """Load one run of Trentina's ``collect-wild`` workflow from its artifacts.

    Those verdicts came from the benchmark harness, which asks the judge
    alone: there is no L1 or L2 column to fill, and the run is recorded under
    the version ``benchmark`` so it is never mistaken for the pipeline's own.

    Args:
        store: The database.
        documents: The run's ``wild.json``.
        judged: Each judge model's ``detonation.json`` from the same run.
        now: When the import is made; the documents' first-seen time.

    Returns:
        The run's id.
    """
    found = json.loads(documents.read_text())
    run_id = store.open_run(found.get("source", "moltbook"), ("benchmark", "benchmark"), now)
    collected = [Document(d["id"], d["kind"], d["url"], d["text"]) for d in found["documents"]]
    new = store.add_documents(found.get("source", "moltbook"), collected, now)
    for path in judged:
        result = json.loads(path.read_text())
        for record in result["verdicts"]:
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
