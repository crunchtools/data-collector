"""The labeller: every document labelled by each labeller model, on the Claude
subscription, by Claude Code with no tools.

The collector image's ``label`` and ``check-label`` commands run it, in a
process of their own with the label unit's environment. It loads no httpx
and no Trentina: this module and ``store`` use the standard library only.
Claude Code is the native binary the image copies to ``/usr/bin/claude``.

The text is hostile, and no agent with a tool in reach ever reads it. This
module is the orchestrator, and it is plain code: it reads documents from the
database, hands a batch to ``claude -p`` with every tool and MCP server
switched off, checks what comes back against a schema, and writes the
labels. Settings come from the environment
(``deploy/labeller.env.example`` lists every one):

* ``COLLECTOR_DB``: the SQLite file. Default ``/data/collector.db``.
* ``COLLECTOR_LABELLERS``: labeller models, comma-separated, as Claude Code
  names them. Default ``claude-opus-5-5,claude-fable-5-1``.
* ``COLLECTOR_LABEL_LIMIT``: documents per labeller per run. Default 800.
* ``COLLECTOR_EVAL_BENIGN``: agreed-benign documents in the evaluation set.
  Default 200.
* ``CLAUDE_CODE_OAUTH_TOKEN``: the subscription's inference token
  (``claude setup-token``). Read by Claude Code, not here.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import secrets
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .store import FAILED, LABELS, MAX_UNANSWERED, UNLABELABLE, Store

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from .store import Unlabelled

PROMPT = (Path(__file__).parent / "label_prompt.md").read_text()
PROMPT_SHA = hashlib.sha256(PROMPT.encode()).hexdigest()[:16]
"""On every label: a changed prompt owes the corpus a new label from everyone."""

LABELLERS = "claude-opus-5-5,claude-fable-5-1"
# Documents in one ask. Few enough that one document arguing for its
# neighbours sits among few of them; each pass shuffles them afresh.
BATCH = 20
# Characters of a reason that are kept. It is the model's words about a
# hostile text, read only by `review`.
REASON_LENGTH = 300
# Seconds one ask may take.
ASK_TIMEOUT = 600
# Asks in a row that may fail outright (Claude Code exits non-zero: the token
# is refused, or the subscription's limit is reached) before a pass stops.
GIVE_UP_AFTER = 2
EXIT_UNFINISHED = 3

OK = "ok"
INCOMPLETE = "incomplete"
LABELLER_FAILED = "labeller failed"
SEVERITY = (OK, INCOMPLETE, LABELLER_FAILED)

SCHEMA = {
    "type": "object",
    "properties": {
        "labels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "label": {"type": "string", "enum": list(LABELS)},
                    "reason": {"type": "string"},
                },
                "required": ["id", "label", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["labels"],
    "additionalProperties": False,
}


class LabellerError(Exception):
    """An ask that failed outright: Claude Code exited non-zero or timed
    out. The class only; nothing it printed is kept, because it may echo the
    documents."""


@dataclass(frozen=True)
class Answer:
    """What one ask returned: the model that answered (as Claude Code reports
    it) and each label it gave, by document id, with its reason."""

    model: str | None
    labels: dict[str, tuple[str, str]]


def batch_message(documents: Sequence[Unlabelled], nonce: str) -> str:
    """The user message for one ask: each document between tags that carry
    ``nonce``, which no document can know in advance."""
    parts = [f"Label these {len(documents)} documents. The nonce is {nonce}.\n"]
    parts.extend(
        f'<document-{nonce} id="{document.id}">\n{document.text}\n</document-{nonce}>\n'
        for document in documents
    )
    return "\n".join(parts)


def read_answer(output: str, wanted: set[str]) -> Answer:
    """What Claude Code's JSON result says, kept only where it is a label
    from ``LABELS`` for a document that was asked about. A result that is an
    error or carries no structured output answers nothing."""
    result = json.loads(output)
    models = list(result.get("modelUsage") or {})
    labels: dict[str, tuple[str, str]] = {}
    given = result.get("structured_output") if not result.get("is_error") else None
    for item in (given or {}).get("labels", []):
        if not isinstance(item, dict):
            continue
        document_id, label = item.get("id"), item.get("label")
        if document_id in wanted and label in LABELS and document_id not in labels:
            labels[str(document_id)] = (str(label), str(item.get("reason", ""))[:REASON_LENGTH])
    return Answer(models[0] if models else None, labels)


async def _claude_code(*args: str, given: str = "", cwd: str | None = None) -> tuple[int, str]:
    """Run Claude Code with ``args`` and ``given`` on stdin, no shell, and
    return its exit status and stdout. Raises ``LabellerError`` when it is
    not found or takes longer than ``ASK_TIMEOUT``."""
    found = shutil.which("claude")
    if found is None:
        raise LabellerError("claude not found")
    env = {**os.environ, "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
    process = await asyncio.create_subprocess_exec(
        found,
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,  # it may echo the documents
        cwd=cwd,
        env=env,
    )
    try:
        out, _ = await asyncio.wait_for(process.communicate(given.encode()), ASK_TIMEOUT)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise LabellerError("timeout") from exc
    return int(process.returncode or 0), out.decode()


def claude(labeller: str, message: str, wanted: set[str]) -> Answer:
    """One ask of Claude Code, as the labeller: no tools, no MCP servers, no
    settings files, no session kept, from an empty directory."""
    flags = (
        "-p",
        "--model",
        labeller,
        "--effort",
        "low",
        "--tools",
        "",
        "--strict-mcp-config",
        "--setting-sources",
        "",
        "--no-session-persistence",
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(SCHEMA),
        "--system-prompt",
        PROMPT,
    )
    with tempfile.TemporaryDirectory() as empty:
        status, out = asyncio.run(_claude_code(*flags, given=message, cwd=empty))
    if status != 0:
        raise LabellerError(f"exit {status}")
    try:
        return read_answer(out, wanted)
    except ValueError as exc:  # not JSON
        raise LabellerError(type(exc).__name__) from exc


def label_pass(
    store: Store,
    run_id: int,
    labeller: str,
    limit: int,
    ask: Callable[[str, str, set[str]], Answer],
    clock: Callable[[], float],
    shuffle: Callable[[list[Unlabelled]], None] = random.shuffle,
) -> str:
    """Label up to ``limit`` documents ``labeller`` owes, newest first, in
    shuffled batches of ``BATCH``.

    A document left out of an answer, or given a label outside ``LABELS``,
    is recorded as ``failed`` and owed again; after ``MAX_UNANSWERED`` runs
    of that it is ``unlabelable``, which sends it to a human. An ask that
    fails outright records nothing: the cause is the subscription, not the
    document. ``GIVE_UP_AFTER`` of those in a row end the pass.

    Returns:
        ``ok``; ``incomplete`` when documents were left owing or the pass
        stopped part way; ``labeller failed`` when not one ask was answered.
    """
    owed = store.unlabelled(labeller, PROMPT_SHA, limit)
    shuffle(owed)
    answered = errors = 0
    outcome = OK
    for start in range(0, len(owed), BATCH):
        batch = owed[start : start + BATCH]
        wanted = {document.id for document in batch}
        try:
            answer = ask(labeller, batch_message(batch, secrets.token_hex(8)), wanted)
        except LabellerError:
            errors += 1
            outcome = INCOMPLETE
            if errors >= GIVE_UP_AFTER:
                break
            continue
        errors = 0
        answered += 1
        now = clock()
        for document in batch:
            if document.id in answer.labels:
                label, reason = answer.labels[document.id]
                store.add_label(
                    run_id,
                    document.id,
                    labeller,
                    PROMPT_SHA,
                    label,
                    now=now,
                    model=answer.model,
                    reason=reason,
                )
                continue
            outcome = INCOMPLETE
            store.add_label(run_id, document.id, labeller, PROMPT_SHA, FAILED, now=now)
            if document.failures + 1 >= MAX_UNANSWERED:
                store.add_label(run_id, document.id, labeller, PROMPT_SHA, UNLABELABLE, now=now)
    if owed and not answered:
        return LABELLER_FAILED
    return outcome


def run(
    store: Store,
    labellers: Sequence[str],
    *,
    limit: int,
    benign_sample: int,
    ask: Callable[[str, str, set[str]], Answer],
    clock: Callable[[], float],
) -> tuple[str, dict[str, int]]:
    """One labelling run: each labeller's pass, then the evaluation set
    brought up to date with the labels as they now stand.

    Returns:
        The run's outcome (the worst of its passes) and counts worth
        logging: never a document's text, id or reason.
    """
    run_id = store.open_run(
        "label", ("-", "-"), clock(), kind="label", pipeline=f"label-{PROMPT_SHA}"
    )
    outcome = "error: interrupted"
    try:
        outcome = max(
            (label_pass(store, run_id, name, limit, ask, clock) for name in labellers),
            key=SEVERITY.index,
            default=OK,
        )
        finals = store.final_labels(labellers, PROMPT_SHA)
        joined = store.refresh_eval_set(finals, benign_sample, clock())
    except Exception as exc:
        outcome = f"error: {type(exc).__name__}"
        raise
    finally:
        store.close_run(run_id, clock(), outcome=outcome)
    counts: dict[str, int] = {"joined the evaluation set": joined}
    for final in finals.values():
        counts[final.label] = counts.get(final.label, 0) + 1
    return outcome, counts


def _check() -> int:
    """Prove the image can label, with no token and no model call: Claude
    Code runs, and the prompt and schema are packaged."""
    try:
        status, out = asyncio.run(_claude_code("--version"))
    except LabellerError as exc:
        print(f"not labelling: {exc}", file=sys.stderr)
        return 1
    print(f"claude {out.strip()}, prompt {PROMPT_SHA}, {len(SCHEMA)} schema keys")
    return status


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == ["check"]:
        return _check()
    if args not in ([], ["run"]):
        print("usage: python -m data_collector [label|check-label]", file=sys.stderr)
        return 2
    labellers = [
        name.strip()
        for name in os.environ.get("COLLECTOR_LABELLERS", LABELLERS).split(",")
        if name.strip()
    ]
    # HOME is on a tmpfs in the container: Claude Code's own state lives
    # there for one run and is gone after it.
    Path.home().mkdir(parents=True, exist_ok=True)
    store = Store(os.environ.get("COLLECTOR_DB", "/data/collector.db"))
    outcome, counts = run(
        store,
        labellers,
        limit=int(os.environ.get("COLLECTOR_LABEL_LIMIT", "800")),
        benign_sample=int(os.environ.get("COLLECTOR_EVAL_BENIGN", "200")),
        ask=claude,
        clock=time.time,
    )
    tally = ", ".join(f"{name} {count}" for name, count in sorted(counts.items()))
    print(f"labelling finished: {outcome}; {tally}")
    return 0 if outcome in (OK, INCOMPLETE) else EXIT_UNFINISHED


if __name__ == "__main__":
    raise SystemExit(main())
