"""Where documents come from.

One source today: Moltbook, a public network whose posts and comments are
written by agents for agents. Its read API needs no account. A second source
is a second class with the same ``documents`` method.

What is kept of a document is its text, the URL it was read from, and an id
that is the hash of the text, so the same text collected twice is one
document. No author name, handle or profile is kept.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

MOLTBOOK_API = "https://www.moltbook.com/api/v1"
# Seconds between requests: the read API allows 60 a minute.
PAUSE = 1.1
PAGE = 50
HOT_POSTS = 5
# With HOT_POSTS, half of the default limit: no one thread fills the sample.
COMMENTS_PER_POST = 30
# Characters. Shorter than this is a reaction, not a document.
SHORTEST = 40
ID_LENGTH = 16


@dataclass(frozen=True)
class Document:
    """One collected text and where it was read."""

    id: str
    kind: str
    url: str
    text: str

    @classmethod
    def of(cls, kind: str, url: str, text: str) -> Document:
        return cls(hashlib.sha256(text.encode()).hexdigest()[:ID_LENGTH], kind, url, text)


@dataclass
class Moltbook:
    """The read API, one paced request at a time.

    ``fetch`` returns the body at a URL and raises on anything but a 200.
    ``pause`` is awaited with ``PAUSE`` before every request after the first.
    """

    fetch: Callable[[str], Awaitable[bytes]]
    pause: Callable[[float], Awaitable[None]] = asyncio.sleep
    name: str = "moltbook"
    asked: int = 0

    async def get(self, path: str) -> dict[str, Any]:
        if self.asked:
            await self.pause(PAUSE)
        self.asked += 1
        return dict(json.loads(await self.fetch(f"{MOLTBOOK_API}/{path}")))

    async def comments(self) -> AsyncIterator[Document]:
        """The newest comments under the hottest posts, replies included, in
        reading order: where text addressed to whoever reads a thread collects."""
        for post in (await self.get(f"posts?sort=hot&limit={HOT_POSTS}"))["posts"]:
            path = f"posts/{post['id']}/comments?sort=new&limit={COMMENTS_PER_POST}"
            unread = list((await self.get(path))["comments"])
            while unread:
                comment = unread.pop(0)
                yield Document.of(
                    "comment", f"{MOLTBOOK_API}/{path}", str(comment.get("content") or "")
                )
                unread[:0] = comment.get("replies") or []

    async def posts(self) -> AsyncIterator[Document]:
        """The newest posts, each fetched whole (the listing cuts them short),
        and only when the caller asks for the next one."""
        cursor = ""
        while True:
            more = f"&cursor={cursor}" if cursor else ""
            page = await self.get(f"posts?sort=new&limit={PAGE}{more}")
            for listed in page["posts"]:
                path = f"posts/{listed['id']}"
                post = (await self.get(path))["post"]
                text = f"{post.get('title') or ''}\n\n{post.get('content') or ''}"
                yield Document.of("post", f"{MOLTBOOK_API}/{path}", text)
            cursor = str(page.get("next_cursor") or "")
            if not (page.get("has_more") and cursor):
                return

    async def documents(self, limit: int) -> list[Document]:
        """Up to ``limit`` distinct documents: comments for at most half of
        it, then posts. Nothing is fetched once there is no room for it."""
        seen: dict[str, Document] = {}
        for found, room in ((self.comments(), limit // 2), (self.posts(), limit)):
            while len(seen) < room and (document := await anext(found, None)) is not None:
                if len(document.text) >= SHORTEST:
                    seen.setdefault(document.id, document)
        return list(seen.values())
