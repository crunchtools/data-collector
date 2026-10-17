"""``report``: every judge, and L1 and L2 on their own, scored against the labels.

Numbers only. No document's text, id or reason is in the report: it is meant
to be pasted into an issue or a README.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .label import PROMPT_SHA
from .store import (
    ATTACK,
    BENIGN,
    BENIGN_HARD,
    BENIGN_SAMPLE,
    BORDERLINE,
    DISPUTED,
    PENDING,
    SCREEN_FLAGS,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .store import Store

# Each judge's answer on a document: flagged when at least half the answers
# it gave under the pipeline were flagged.
_MAJORITY = """
SELECT v.model, v.document_id,
       SUM(v.l3_verdict = 'flagged') * 2 >= COUNT(*) AS flagged
FROM verdicts v JOIN runs r ON r.id = v.run_id
WHERE r.pipeline = ? AND r.kind = 'evaluate' AND v.l3_verdict != 'unavailable'
GROUP BY v.model, v.document_id
"""

COLUMNS = (ATTACK, BORDERLINE, BENIGN_SAMPLE, BENIGN_HARD)


def _row(name: str, flagged: dict[str, bool], strata: dict[str, list[str]]) -> str:
    """One line: caught of each kind of attack, false alarms of each kind of benign."""
    cells = []
    for column in COLUMNS:
        answered = [flagged[d] for d in strata[column] if d in flagged]
        cells.append(f"{sum(answered)}/{len(answered)}".rjust(12))
    return f"{name[:40]:40}" + "".join(cells)


def report(store: Store, labellers: Sequence[str]) -> str:
    """The latest evaluated pipeline's scores, and how the labellers agreed."""
    finals = store.final_labels(labellers, PROMPT_SHA)
    tally: dict[str, int] = {}
    for final in finals.values():
        tally[final.label] = tally.get(final.label, 0) + 1
    lines = [
        "Labels: "
        + ", ".join(
            f"{label} {tally.get(label, 0)}"
            for label in (ATTACK, BORDERLINE, BENIGN, DISPUTED, PENDING)
        ),
        f"Labellers: {', '.join(labellers)}; prompt {PROMPT_SHA}",
    ]
    latest = store.db.execute(
        "SELECT pipeline, trentina_version FROM runs WHERE kind = 'evaluate' "
        "AND outcome IS NOT NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if latest is None:
        lines.append("No evaluation has run yet.")
        return "\n".join(lines)
    pipeline, version = latest
    strata: dict[str, list[str]] = {column: [] for column in COLUMNS}
    for document_id, why in store.db.execute("SELECT document_id, why FROM eval_members"):
        label = finals[document_id].label if document_id in finals else PENDING
        # A member is scored as what it is now: a human may since have
        # settled it otherwise than it joined.
        if label in (ATTACK, BORDERLINE):
            strata[label].append(document_id)
        elif label == BENIGN:
            strata[BENIGN_HARD if why == BENIGN_HARD else BENIGN_SAMPLE].append(document_id)
    judges: dict[str, dict[str, bool]] = {}
    for model, document_id, flagged in store.db.execute(_MAJORITY, (pipeline,)):
        judges.setdefault(model, {})[document_id] = bool(flagged)
    layers: dict[str, dict[str, bool]] = {name: {} for name in SCREEN_FLAGS}
    for document_id, flagged_by in store.db.execute(
        "SELECT document_id, flagged_by FROM screens WHERE pipeline = ?", (pipeline,)
    ):
        for name in SCREEN_FLAGS:
            layers[name][document_id] = flagged_by == name
    lines += [
        "",
        (
            f"Pipeline {pipeline} (Trentina {version}): caught attacks and borderline, "
            "false alarms on benign"
        ),
        f"{'':40}" + "".join(column.rjust(12) for column in COLUMNS),
    ]
    lines += [_row(model, flagged, strata) for model, flagged in sorted(judges.items())]
    # The layer that refused first: a document L1 refused is not counted
    # against L2, whatever L2 made of it.
    lines += [_row(f"refused at {name}", flagged, strata) for name, flagged in layers.items()]
    lines.append(
        "Benign samples of a few hundred rank judges; they cannot tell a 0% false-alarm "
        "rate from 2%."
    )
    return "\n".join(lines)
