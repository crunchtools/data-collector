"""The database: every document collected, what each labeller called it, and
every verdict on it.

One SQLite file. A document is stored once however often it is collected. A
verdict is one ask of one judge model about one document, under one defense
pipeline: the pipeline changes, and what an older one decided is kept, not
overwritten. A label is one labeller's reading of one document under one
labelling prompt; labels are the answer key the verdicts are scored against.

Document text is hostile. It is stored here and nowhere else: never logged,
never printed, except to a human's terminal by ``review``.

The standard library only: the labeller's image imports this module and has
no httpx and no Trentina.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from .feed import Document

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    kind TEXT NOT NULL,
    url TEXT NOT NULL,
    text TEXT NOT NULL,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    times_seen INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL,
    trentina_version TEXT NOT NULL,
    perimeter_version TEXT NOT NULL,
    collected INTEGER,
    new_documents INTEGER,
    cost_usd REAL,
    outcome TEXT
);

CREATE TABLE IF NOT EXISTS run_models (
    run_id INTEGER NOT NULL REFERENCES runs(id),
    model TEXT NOT NULL,
    asks INTEGER NOT NULL,
    answered INTEGER NOT NULL,
    cost_usd REAL,
    PRIMARY KEY (run_id, model)
);

CREATE TABLE IF NOT EXISTS verdicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    document_id TEXT NOT NULL REFERENCES documents(id),
    model TEXT NOT NULL,
    flagged_by TEXT,
    l1_risk TEXT,
    l2_label TEXT,
    l2_score REAL,
    l3_verdict TEXT NOT NULL,
    l3_risk TEXT,
    l3_detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_verdicts_document ON verdicts(document_id, model);
"""

# Each entry takes the database from the version before it to its own index
# + 1, and says so itself (``PRAGMA user_version``), inside one transaction:
# a migration and its stamp land together or not at all. A new database runs
# them all; an existing one runs what it has not. Never edit one that has
# shipped: add the next.
MIGRATIONS = (
    # 1: labels, screens and the evaluation set (#9). A run says what it was
    # (`kind`) and which defense pipeline judged under it (`pipeline`), so a
    # Trentina release that changes nothing a verdict depends on owes nothing.
    """
    BEGIN;
    ALTER TABLE runs ADD COLUMN kind TEXT NOT NULL DEFAULT 'collect';
    ALTER TABLE runs ADD COLUMN pipeline TEXT;
    UPDATE runs SET pipeline = CASE trentina_version
        WHEN 'benchmark' THEN 'benchmark' ELSE 'trentina-' || trentina_version END;
    -- A judge is its model and its effort: the one reasoning model ever run
    -- here ran at minimal.
    UPDATE verdicts SET model = model || ':minimal' WHERE model = 'google/gemini-3.8-flash';
    UPDATE run_models SET model = model || ':minimal' WHERE model = 'google/gemini-3.8-flash';

    CREATE TABLE labels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER REFERENCES runs(id),
        document_id TEXT NOT NULL REFERENCES documents(id),
        labeller TEXT NOT NULL,
        model TEXT,
        prompt_sha TEXT NOT NULL,
        label TEXT NOT NULL,
        reason TEXT,
        labelled_at REAL NOT NULL
    );
    CREATE INDEX idx_labels_document ON labels(document_id, labeller, prompt_sha);

    CREATE TABLE screens (
        document_id TEXT NOT NULL REFERENCES documents(id),
        pipeline TEXT NOT NULL,
        flagged_by TEXT,
        l1_risk TEXT,
        l2_label TEXT,
        l2_score REAL,
        PRIMARY KEY (document_id, pipeline)
    );

    CREATE TABLE eval_members (
        document_id TEXT PRIMARY KEY REFERENCES documents(id),
        why TEXT NOT NULL,
        added_at REAL NOT NULL
    );
    PRAGMA user_version = 1;
    COMMIT;
    """,
)

UNAVAILABLE = "unavailable"
"""The ``l3_verdict`` of an ask the judge did not answer. It is a row too:
the ask was made, and it is owed again. ``flagged`` and ``clean`` are answers."""

ATTACK = "attack"
BORDERLINE = "borderline"
BENIGN = "benign"
UNLABELABLE = "unlabelable"
LABELS = (ATTACK, BORDERLINE, BENIGN)
"""What a labeller may call a document. ``unlabelable`` is what the labeller
run records after ``MAX_UNANSWERED`` runs without an answer; a human decides."""
FAILED = "failed"
"""The label of an ask a labeller did not answer: like ``unavailable``, a row,
owed again, and not an answer."""
HUMAN = "human"
DISPUTED = "disputed"
PENDING = "pending"

# Ways a document joins the evaluation set.
BENIGN_HARD = "benign-hard"
BENIGN_SAMPLE = "benign-sample"

# Layers that would have refused a document in a screen (no judge asked).
SCREEN_FLAGS = ("L1", "L2")

MAX_UNANSWERED = 3
"""Runs in which one judge, or one labeller, may leave one document unanswered
before the document is left alone. Runs, not asks: an outage that swallows a
day's three asks is one failure, not three. Some documents can never be
answered (a reply the judge cannot form, a canary it leaks), and asking daily
for ever would both waste the budget and, with a few of them at the head of
the queue, stop the judge reaching anything new."""

# Documents a model still owes answers on: fewer answers than asked for and
# fewer than MAX_UNANSWERED runs that failed on it, counting only asks made
# under the same defense pipeline as the given run. An evaluation run is owed
# answers on the evaluation set only. Newest first, so a backlog never stands
# between a judge and today's documents.
_PENDING = """
SELECT d.id, d.url, d.text,
       :votes - COALESCE(SUM(v.l3_verdict != 'unavailable'), 0) AS owed
FROM documents d
LEFT JOIN verdicts v
    ON v.document_id = d.id AND v.model = :model
    AND v.run_id IN (
        SELECT id FROM runs WHERE pipeline = (SELECT pipeline FROM runs WHERE id = :run_id)
    )
WHERE (SELECT kind FROM runs WHERE id = :run_id) != 'evaluate'
   OR d.id IN (SELECT document_id FROM eval_members)
GROUP BY d.id
HAVING owed > 0 AND COUNT(DISTINCT CASE WHEN v.l3_verdict = 'unavailable' THEN v.run_id END)
    < :max_unanswered
ORDER BY d.first_seen DESC, d.id
LIMIT :most
"""

# Documents a labeller has not labelled under this prompt, and has not failed
# on in MAX_UNANSWERED runs. Newest first.
_UNLABELLED = """
SELECT d.id, d.text,
       COUNT(DISTINCT CASE WHEN l.label = 'failed' THEN l.run_id END) AS failures
FROM documents d
LEFT JOIN labels l
    ON l.document_id = d.id AND l.labeller = :labeller AND l.prompt_sha = :prompt_sha
GROUP BY d.id
HAVING COALESCE(SUM(l.label != 'failed'), 0) = 0
ORDER BY d.first_seen DESC, d.id
LIMIT :most
"""

# Documents with no screen under this pipeline. Newest first.
_UNSCREENED = """
SELECT d.id, d.url, d.text, 1 AS owed FROM documents d
WHERE NOT EXISTS (
    SELECT 1 FROM screens s WHERE s.document_id = d.id AND s.pipeline = :pipeline
)
ORDER BY d.first_seen DESC, d.id
LIMIT :most
"""

# What one ask of a model has cost, over every pass that was paid for. An
# average, not the dearest pass: the key's usage figure lags, so a small
# pass can be charged for its neighbour's asks.
_ASK_COST = """
SELECT SUM(cost_usd) / SUM(asks) FROM run_models
WHERE model = ? AND cost_usd IS NOT NULL AND asks > 0
"""

_ANSWER_COUNTS = """
SELECT COUNT(*), COALESCE(SUM(l3_verdict != 'unavailable'), 0)
FROM verdicts WHERE run_id = ? AND model = ?
"""


@dataclass(frozen=True)
class Pending:
    """A document a judge model still owes answers on."""

    id: str
    url: str
    text: str
    owed: int


@dataclass(frozen=True)
class Unlabelled:
    """A document a labeller still owes a label on, and how many runs it has
    already failed to give one in."""

    id: str
    text: str
    failures: int


@dataclass(frozen=True)
class Final:
    """What a document is, as far as the labels go: ``label`` is one of
    ``LABELS`` when a human said so or every labeller agreed, else
    ``disputed`` or ``pending``. ``by`` is ``human`` or ``labellers``."""

    label: str
    by: str


class Store:
    """The collector's database at ``path``, created on first use and
    migrated to the latest schema."""

    def __init__(self, path: Path | str) -> None:
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        version = int(self.db.execute("PRAGMA user_version").fetchone()[0])
        for script in MIGRATIONS[version:]:
            self.db.executescript(script)

    def add_documents(self, source: str, found: Iterable[Document], now: float) -> int:
        """Record each document as seen at ``now``. Returns how many were new."""
        new = 0
        for document in found:
            known = self.db.execute(
                "UPDATE documents SET last_seen = ?, times_seen = times_seen + 1 WHERE id = ?",
                (now, document.id),
            ).rowcount
            if not known:
                new += 1
                self.db.execute(
                    "INSERT INTO documents (id, source, kind, url, text, first_seen, last_seen) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (document.id, source, document.kind, document.url, document.text, now, now),
                )
        self.db.commit()
        return new

    def open_run(
        self,
        source: str,
        versions: tuple[str, str],
        now: float,
        *,
        kind: str = "collect",
        pipeline: str | None = None,
    ) -> int:
        """Start a run of ``kind`` under ``versions`` (Trentina's, its
        perimeter's) and ``pipeline``, the fingerprint of the defense
        pipeline (``pipeline.fingerprint``; Trentina's version when None)."""
        cursor = self.db.execute(
            "INSERT INTO runs (source, started_at, trentina_version, perimeter_version, kind, "
            "pipeline) VALUES (?, ?, ?, ?, ?, ?)",
            (source, now, *versions, kind, pipeline or f"trentina-{versions[0]}"),
        )
        self.db.commit()
        return int(cursor.lastrowid or 0)

    def close_run(
        self,
        run_id: int,
        now: float,
        *,
        collected: int | None = None,
        new_documents: int | None = None,
        outcome: str,
        cost_usd: float | None = None,
    ) -> None:
        """Finish a run with what it collected, what it cost and how it ended."""
        self.db.execute(
            "UPDATE runs SET finished_at = :now, collected = :collected, "
            "new_documents = :new_documents, cost_usd = :cost_usd, outcome = :outcome "
            "WHERE id = :run_id",
            {
                "now": now,
                "collected": collected,
                "new_documents": new_documents,
                "cost_usd": cost_usd,
                "outcome": outcome,
                "run_id": run_id,
            },
        )
        self.db.commit()

    def pending(self, run_id: int, model: str, votes: int, most: int) -> list[Pending]:
        """Up to ``most`` documents with fewer than ``votes`` answers from
        ``model`` under the pipeline of ``run_id``, newest first, leaving out
        any it has left unanswered in ``MAX_UNANSWERED`` runs. For an
        evaluation run, only documents in the evaluation set."""
        asked = {
            "votes": votes,
            "model": model,
            "run_id": run_id,
            "max_unanswered": MAX_UNANSWERED,
            "most": most,
        }
        rows = self.db.execute(_PENDING, asked).fetchall()
        return [Pending(row["id"], row["url"], row["text"], row["owed"]) for row in rows]

    def ask_cost(self, model: str) -> float | None:
        """Dollars one ask of ``model`` has cost on average, or None before
        its first paid pass."""
        cost = self.db.execute(_ASK_COST, (model,)).fetchone()[0]
        return None if cost is None else float(cost)

    def add_verdict(
        self, run_id: int, document_id: str, model: str, verdict: dict[str, Any]
    ) -> None:
        """One ask's result. ``verdict`` carries the verdict columns;
        ``l3_detail`` says why an unanswered ask went unanswered."""
        names = (
            "flagged_by",
            "l1_risk",
            "l2_label",
            "l2_score",
            "l3_verdict",
            "l3_risk",
            "l3_detail",
        )
        self.db.execute(
            "INSERT INTO verdicts (run_id, document_id, model, flagged_by, l1_risk, l2_label, "
            "l2_score, l3_verdict, l3_risk, l3_detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, document_id, model, *(verdict.get(name) for name in names)),
        )
        self.db.commit()

    def close_model(self, run_id: int, model: str, cost_usd: float | None) -> tuple[int, int]:
        """Record one model's pass of a run from its verdict rows. Returns
        (asks, answered)."""
        asks, answered = self.db.execute(_ANSWER_COUNTS, (run_id, model)).fetchone()
        self.db.execute(
            "INSERT OR REPLACE INTO run_models (run_id, model, asks, answered, cost_usd) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, model, asks, answered, cost_usd),
        )
        self.db.commit()
        return int(asks), int(answered)

    # Screens: L1 and L2 with no judge, on every document, at no cost.

    def unscreened(self, pipeline: str, most: int) -> list[Pending]:
        """Up to ``most`` documents with no screen under ``pipeline``, newest first."""
        unscreened = self.db.execute(_UNSCREENED, {"pipeline": pipeline, "most": most})
        # A screen is one ask, owed once.
        return [Pending(id_, url, text, owed=1) for id_, url, text, _ in unscreened]

    def add_screen(self, document_id: str, pipeline: str, verdict: dict[str, Any]) -> None:
        """What L1 and L2 made of a document under ``pipeline``."""
        self.db.execute(
            "INSERT OR REPLACE INTO screens (document_id, pipeline, flagged_by, l1_risk, "
            "l2_label, l2_score) VALUES (?, ?, ?, ?, ?, ?)",
            (
                document_id,
                pipeline,
                verdict.get("flagged_by") if verdict.get("flagged_by") in SCREEN_FLAGS else None,
                verdict.get("l1_risk"),
                verdict.get("l2_label"),
                verdict.get("l2_score"),
            ),
        )
        self.db.commit()

    def screened_flagged(self) -> set[str]:
        """Documents L1 or L2 would have refused under any pipeline's screen."""
        rows = self.db.execute(
            "SELECT DISTINCT document_id FROM screens WHERE flagged_by IS NOT NULL"
        )
        return {row[0] for row in rows}

    # Labels.

    def unlabelled(self, labeller: str, prompt_sha: str, most: int) -> list[Unlabelled]:
        """Up to ``most`` documents ``labeller`` owes a label on under
        ``prompt_sha``, newest first, with the runs it has failed on each."""
        owed = self.db.execute(
            _UNLABELLED, {"labeller": labeller, "prompt_sha": prompt_sha, "most": most}
        )
        return [Unlabelled(id=row[0], text=row[1], failures=row[2]) for row in owed]

    def add_label(
        self,
        run_id: int | None,
        document_id: str,
        labeller: str,
        prompt_sha: str,
        label: str,
        *,
        now: float,
        model: str | None = None,
        reason: str | None = None,
    ) -> None:
        """One labeller's label of one document, or ``failed`` for an ask it
        did not answer."""
        self.db.execute(
            "INSERT INTO labels (run_id, document_id, labeller, model, prompt_sha, label, "
            "reason, labelled_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, document_id, labeller, model, prompt_sha, label, reason, now),
        )
        self.db.commit()

    def final_labels(self, labellers: Iterable[str], prompt_sha: str) -> dict[str, Final]:
        """What every document is, by the labels so far.

        A human's latest label wins. Otherwise every labeller in
        ``labellers`` must have labelled the document under ``prompt_sha``
        (``pending`` until then), and they must agree (``disputed`` when they
        do not, or when one gave up). A document they agree is benign but
        that L1 or L2 would have refused is ``disputed`` too: two models
        fooled the same way by the text agree, and the screens cost nothing
        to ask.
        """
        wanted = tuple(labellers)
        humans: dict[str, str] = {}
        rows = self.db.execute(
            "SELECT document_id, label FROM labels WHERE labeller = 'human' ORDER BY id"
        )
        for row in rows:
            humans[row["document_id"]] = row["label"]
        given: dict[str, dict[str, str]] = {}
        rows = self.db.execute(
            "SELECT document_id, labeller, label FROM labels "
            "WHERE prompt_sha = ? AND label != 'failed' ORDER BY id",
            (prompt_sha,),
        )
        for row in rows:
            given.setdefault(row["document_id"], {})[row["labeller"]] = row["label"]
        flagged = self.screened_flagged()
        finals: dict[str, Final] = {}
        for (document_id,) in self.db.execute("SELECT id FROM documents"):
            if document_id in humans:
                finals[document_id] = Final(humans[document_id], HUMAN)
                continue
            mine = given.get(document_id, {})
            answers = {mine.get(labeller) for labeller in wanted}
            if None in answers or not wanted:
                finals[document_id] = Final(PENDING, "labellers")
            elif len(answers) > 1 or UNLABELABLE in answers:
                finals[document_id] = Final(DISPUTED, "labellers")
            elif answers == {BENIGN} and document_id in flagged:
                finals[document_id] = Final(DISPUTED, "screens")
            else:
                finals[document_id] = Final(answers.pop() or PENDING, "labellers")
        return finals

    def labels_of(self, document_id: str, prompt_sha: str) -> list[tuple[str, str, str | None]]:
        """Each labeller's label and reason for one document under ``prompt_sha``."""
        rows = self.db.execute(
            "SELECT labeller, label, reason FROM labels WHERE document_id = ? "
            "AND (prompt_sha = ? OR labeller = 'human') AND label != 'failed' ORDER BY id",
            (document_id, prompt_sha),
        )
        return [(row["labeller"], row["label"], row["reason"]) for row in rows]

    def text_of(self, document_id: str) -> str:
        """A document's text. Only ``review`` asks, to show it to a human."""
        return str(
            self.db.execute("SELECT text FROM documents WHERE id = ?", (document_id,)).fetchone()[0]
        )

    # The evaluation set.

    def refresh_eval_set(self, finals: dict[str, Final], benign_sample: int, now: float) -> int:
        """Add what has become worth judging to the evaluation set, and return
        how many joined.

        Attacks and borderline documents join, and so does every document a
        human settled as benign (``benign-hard``: the text that fooled a
        judge or split the labellers). A sample of documents the labellers
        agreed are benign is drawn once, in id order (a hash, so in effect at
        random), and topped up to ``benign_sample``. Members never leave, so
        two pipelines' scores are measured on the same documents.
        """
        members = {
            row[0]: row[1] for row in self.db.execute("SELECT document_id, why FROM eval_members")
        }
        joining: list[tuple[str, str]] = []
        for document_id, final in sorted(finals.items()):
            if document_id in members:
                continue
            if final.label in (ATTACK, BORDERLINE):
                joining.append((document_id, final.label))
            elif final.label == BENIGN and final.by == HUMAN:
                joining.append((document_id, BENIGN_HARD))
        room = benign_sample - sum(why == BENIGN_SAMPLE for why in members.values())
        taken = {document_id for document_id, _ in joining}
        for document_id, final in sorted(finals.items()):
            if room <= 0:
                break
            if document_id in members or document_id in taken:
                continue
            if final.label == BENIGN and final.by == "labellers":
                joining.append((document_id, BENIGN_SAMPLE))
                room -= 1
        self.db.executemany(
            "INSERT INTO eval_members (document_id, why, added_at) VALUES (?, ?, ?)",
            [(document_id, why, now) for document_id, why in joining],
        )
        self.db.commit()
        return len(joining)

    def eval_size(self) -> int:
        """How many documents the evaluation set holds."""
        return int(self.db.execute("SELECT COUNT(*) FROM eval_members").fetchone()[0])
