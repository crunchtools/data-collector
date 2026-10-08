"""The collector's use of Trentina: the only module that imports it.

Trentina is distributed as a container image and nothing else, so this image
is built on Trentina's and calls its code in place. ``judge`` runs the
shipped defense pipeline on one document: the deterministic layer, the local
classifier, and the quarantined judge model with its prompt pack. The
verdict is what a Trentina gateway of this version would have decided.

Which judge model answers is Trentina's own configuration, read from the
environment (``QUARANTINE_PROVIDER``, ``QUARANTINE_MODEL``,
``QUARANTINE_REASONING_EFFORT``, ``OPENROUTER_API_KEY``). It is read once
per process, which is why each model's pass runs in a process of its own.
"""

from __future__ import annotations

from typing import Any

import trentina
from trentina.defense import defend
from trentina.egress import open_guarded
from trentina.perimeter_db import PERIMETER_VERSION

# Bytes. A feed's API answers in kilobytes; past this something is wrong.
MAX_BODY = 5 * 1024 * 1024
HTTP_OK = 200


def versions() -> tuple[str, str]:
    """Trentina's version and its perimeter's: what a verdict was reached under."""
    return str(trentina.__version__), str(PERIMETER_VERSION)


async def fetch(url: str) -> bytes:
    """The body at ``url``, through Trentina's egress guard: every hop of a
    redirect is refused unless it resolves to a public address."""
    body = bytearray()
    async with open_guarded("GET", url, timeout=30.0, deadline=120.0) as response:
        if response.status_code != HTTP_OK:
            raise ValueError(f"feed answered {response.status_code}")
        async for chunk in response.aiter_bytes():
            body += chunk
            if len(body) > MAX_BODY:
                raise ValueError("feed answered with an oversized body")
    return bytes(body)


async def judge(text: str, source: str) -> dict[str, Any]:
    """Every layer's opinion of ``text``, as the ``verdicts`` columns.

    Nothing is recorded in Trentina's own database and nothing is blocked:
    the collector asks what the pipeline thinks, and keeps the answer itself.
    """
    verdict = await defend(text, source=source, source_type="url", record=False)
    assessment = verdict.l3_assessment
    if assessment is None or assessment.get("l3_unavailable"):
        l3 = "unavailable"
    else:
        l3 = "flagged" if assessment.get("injection_detected") else "clean"
    classification = verdict.classification
    return {
        "flagged_by": verdict.flagged_by.value if verdict.flagged_by is not None else None,
        "l1_risk": verdict.pipeline.stats.risk_level(),
        "l2_label": classification.label if classification is not None else None,
        "l2_score": classification.score if classification is not None else None,
        "l3_verdict": l3,
        "l3_risk": assessment.get("risk_level") if l3 == "flagged" and assessment else None,
    }
