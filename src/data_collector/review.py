"""``review``: a human labels what the labellers could not settle.

The one place document text is shown: to a person, at a terminal, one
document at a time, on request. It is never shown anywhere else, and never to
an agent. What is shown is cleaned first: a document, or a labeller's reason
written about one, can carry control and escape sequences that a terminal
would act on (set the clipboard, retitle the window, answer a query back as
keystrokes), and bidirectional or invisible characters that make it read as
something it is not.
"""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING, TextIO

from .label import PROMPT_SHA
from .store import ATTACK, BENIGN, BORDERLINE, DISPUTED, HUMAN

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from .store import Store

CHOICES = {"a": ATTACK, "b": BORDERLINE, "n": BENIGN}
KEPT = {"\n", "\t"}


def shown(text: str) -> str:
    """``text`` with every control character (C0, C1, ESC, DEL) and every
    format character (bidirectional overrides, zero-width marks) removed,
    keeping newlines and tabs."""
    return "".join(
        character
        for character in text
        if character in KEPT or unicodedata.category(character) not in ("Cc", "Cf")
    )


def review(
    store: Store,
    labellers: Sequence[str],
    given: TextIO,
    out: TextIO,
    clock: Callable[[], float],
) -> int:
    """Show each disputed document and record the human's label. Returns the
    exit status: 2 when not at a terminal, else 0."""
    if not (given.isatty() and out.isatty()):
        out.write("review shows hostile text to a person: run it with podman run -it\n")
        return 2
    disputed = [
        (document_id, final)
        for document_id, final in sorted(store.final_labels(labellers, PROMPT_SHA).items())
        if final.label == DISPUTED
    ]
    for number, (document_id, final) in enumerate(disputed, start=1):
        out.write(f"\n=== {number} of {len(disputed)}: {document_id}, disputed by {final.by}\n")
        for labeller, label, reason in store.labels_of(document_id, PROMPT_SHA):
            out.write(f"  {shown(labeller)}: {label}  {shown(reason or '')}\n")
        out.write(f"--- text\n{shown(store.text_of(document_id))}\n--- end\n")
        choice = ""
        while choice not in (*CHOICES, "s", "q"):
            out.write("a attack, b borderline, n benign, s skip, q quit: ")
            out.flush()
            line = given.readline()
            choice = line.strip().lower() if line else "q"  # end of input is a quit
        if choice == "q":
            break
        if choice == "s":
            continue
        out.write("reason (optional): ")
        out.flush()
        reason = given.readline().strip() or None
        store.add_label(
            None, document_id, HUMAN, PROMPT_SHA, CHOICES[choice], now=clock(), reason=reason
        )
    out.write("review finished\n")
    return 0
