"""What the Moltbook reader keeps, and what it leaves out."""

from __future__ import annotations

import json
from typing import Any

from data_collector.feed import MOLTBOOK_API, PAUSE, Document, FeedError, Moltbook

LONG = "A comment long enough to be a document, addressed to whoever reads this thread."
REPLY = "A reply under it, also long enough to be a document in its own right, at depth one."
POST = "The body of a new post, fetched whole because the listing cuts it short at a few lines."


def _api(path: str) -> dict[str, Any]:
    author = {"name": "somebody", "description": "a profile"}
    if path.startswith("posts?sort=hot"):
        return {"posts": [{"id": "h1", "author": author}]}
    if path.startswith("posts/h1/comments"):
        thread = [
            {"content": LONG, "author": author, "replies": [{"content": REPLY, "replies": []}]},
            {"content": "+1", "replies": []},
            {"content": LONG, "replies": []},
        ]
        return {"comments": thread}
    if path.startswith("posts?sort=new"):
        more = "cursor=" not in path
        listed = [{"id": "n1" if more else "n2", "content": "cut sh"}]
        return {"posts": listed, "has_more": more, "next_cursor": "c2" if more else None}
    return {"post": {"id": path, "title": f"Title {path}", "content": POST, "author": author}}


async def _collect(limit: int) -> tuple[list[Document], list[str], list[float]]:
    asked: list[str] = []
    waited: list[float] = []

    async def fetch(url: str) -> bytes:
        asked.append(url.removeprefix(f"{MOLTBOOK_API}/"))
        return json.dumps(_api(asked[-1])).encode()

    async def pause(seconds: float) -> None:
        waited.append(seconds)

    return await Moltbook(fetch, pause).documents(limit), asked, waited


async def test_comments_and_whole_posts_are_documents_and_the_same_text_is_one() -> None:
    documents, asked, waited = await _collect(10)
    assert [d.kind for d in documents] == ["comment", "comment", "post", "post"]
    assert [d.text for d in documents[:2]] == [LONG, REPLY]
    assert documents[2].text == f"Title posts/n1\n\n{POST}"
    assert documents[2].url == f"{MOLTBOOK_API}/posts/n1"
    assert len({d.id for d in documents}) == len(documents)
    assert asked[-1] == "posts/n2"  # the second page was followed, and it was the last
    assert waited == [PAUSE] * (len(asked) - 1)


async def test_a_document_carries_nothing_about_who_wrote_it() -> None:
    documents, _, _ = await _collect(10)
    assert "somebody" not in str(documents)


async def test_collection_stops_at_the_limit_with_comments_at_most_half() -> None:
    documents, asked, _ = await _collect(2)
    assert [d.kind for d in documents] == ["comment", "post"]
    assert "posts/n2" not in asked


def test_a_documents_id_is_its_text() -> None:
    assert Document("post", "u1", LONG).id == Document("comment", "u2", LONG).id
    assert Document("post", "u1", LONG).id != Document("post", "u1", REPLY).id


async def test_one_request_that_fails_costs_one_document_not_the_run() -> None:
    """A post deleted between the listing and the read is a 404. It is
    counted and skipped; everything else is still collected."""

    async def fetch(url: str) -> bytes:
        path = url.removeprefix(f"{MOLTBOOK_API}/")
        if path == "posts/n1":
            raise FeedError("status 404")
        return json.dumps(_api(path)).encode()

    async def pause(_seconds: float) -> None:
        return None

    feed = Moltbook(fetch, pause)
    documents = await feed.documents(10)
    assert [d.url.rsplit("/", 1)[-1] for d in documents if d.kind == "post"] == ["n2"]
    assert feed.failed == 1


async def test_text_that_cannot_be_encoded_is_stored_not_fatal() -> None:
    """JSON may carry a lone surrogate. One hostile comment must not stop a run."""

    async def fetch(url: str) -> bytes:
        path = url.removeprefix(f"{MOLTBOOK_API}/")
        if path.startswith("posts/h1/comments"):
            lone = "\\ud83d"
            return ('{"comments": [{"content": "' + lone + LONG + '", "replies": []}]}').encode()
        return json.dumps(_api(path)).encode()

    async def pause(_seconds: float) -> None:
        return None

    documents = await Moltbook(fetch, pause).documents(2)
    assert documents[0].text.endswith(LONG)
    assert len(documents[0].id) == 16


async def test_whitespace_is_not_a_document() -> None:
    async def fetch(url: str) -> bytes:
        path = url.removeprefix(f"{MOLTBOOK_API}/")
        if path.startswith("posts/h1/comments"):
            return json.dumps({"comments": [{"content": " " * 80, "replies": []}]}).encode()
        return json.dumps(_api(path)).encode()

    async def pause(_seconds: float) -> None:
        return None

    documents = await Moltbook(fetch, pause).documents(4)
    assert all(d.text.strip() for d in documents)
