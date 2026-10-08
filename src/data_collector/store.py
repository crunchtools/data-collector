"""The database: every document collected, and every verdict on it.

One SQLite file. A document is stored once however often it is collected. A
verdict is one ask of one judge model about one document, under one version
of Trentina: the pipeline changes, and what an older one decided is kept,
not overwritten.

Document text is hostile. It is stored here and nowhere else: never logged,
never printed.
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
    l3_risk TEXT
);

CREATE INDEX IF NOT EXISTS idx_verdicts_document ON verdicts(document_id, model);
"""

UNAVAILABLE = "unavailable"
"""The ``l3_verdict`` of an ask the judge did not answer. It is a row too:
the ask was made, and it is owed again. ``flagged`` and ``clean`` are answers."""

# Documents with fewer answers from a model than asked for, counting only
# verdicts reached under the same Trentina version as the given run.
_PENDING = """
SELECT d.id, d.url, d.text, ? - COUNT(v.id) AS owed
FROM documents d
LEFT JOIN verdicts v
    ON v.document_id = d.id AND v.model = ? AND v.l3_verdict != 'unavailable'
    AND v.run_id IN (
        SELECT id FROM runs
        WHERE trentina_version = (SELECT trentina_version FROM runs WHERE id = ?)
    )
GROUP BY d.id HAVING owed > 0
ORDER BY d.first_seen, d.id
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


class Store:
    """The collector's database at ``path``, created on first use."""

    def __init__(self, path: Path | str) -> None:
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

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

    def open_run(self, source: str, versions: tuple[str, str], now: float) -> int:
        """Start a run judged under ``versions`` (Trentina's, its perimeter's)."""
        cursor = self.db.execute(
            "INSERT INTO runs (source, started_at, trentina_version, perimeter_version) "
            "VALUES (?, ?, ?, ?)",
            (source, now, *versions),
        )
        self.db.commit()
        return int(cursor.lastrowid or 0)

    def close_run(self, run_id: int, now: float, **columns: Any) -> None:
        """Finish a run: ``collected``, ``new_documents``, ``cost_usd``, ``outcome``."""
        names = ("collected", "new_documents", "cost_usd", "outcome")
        self.db.execute(
            "UPDATE runs SET finished_at = ?, collected = ?, new_documents = ?, cost_usd = ?, "
            "outcome = ? WHERE id = ?",
            (now, *(columns.get(name) for name in names), run_id),
        )
        self.db.commit()

    def pending(self, run_id: int, model: str, votes: int) -> list[Pending]:
        """Documents with fewer than ``votes`` answers from ``model`` under the
        Trentina version of ``run_id``, oldest first."""
        rows = self.db.execute(_PENDING, (votes, model, run_id)).fetchall()
        return [Pending(row["id"], row["url"], row["text"], row["owed"]) for row in rows]

    def add_verdict(
        self, run_id: int, document_id: str, model: str, verdict: dict[str, Any]
    ) -> None:
        """One ask's result. ``verdict`` carries the six verdict columns."""
        names = ("flagged_by", "l1_risk", "l2_label", "l2_score", "l3_verdict", "l3_risk")
        self.db.execute(
            "INSERT INTO verdicts (run_id, document_id, model, flagged_by, l1_risk, l2_label, "
            "l2_score, l3_verdict, l3_risk) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
