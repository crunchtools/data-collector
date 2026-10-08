# Collecting

A run takes up to `COLLECTOR_LIMIT` documents (300 by default) from Moltbook,
a public network whose posts and comments are written by AI agents for other
agents. Its read API needs no account.

Two samples, half of the limit each:

- the newest comments under the five hottest posts, replies included: text
  addressed to whoever reads a thread collects there;
- the newest posts, each fetched whole, because the listing cuts them short.

Requests are paced at about one a second, under the API's limit of 60 a
minute, and go through Trentina's egress guard, which refuses any address
that is not public, on every hop of a redirect.

## What is kept

For each document: its text, the API URL it was read from, whether it was a
post or a comment, and an id that is the first 16 hex digits of the SHA-256
of the text. The same text collected twice is one document: `times_seen`
goes up and it is not judged, or paid for, a second time.

Not kept: the author's name, handle, profile or any other account data.
Text shorter than 40 characters is skipped as a reaction, not a document.

## What this is not

A document's text is hostile until shown otherwise. It is stored in the
database and nowhere else: never logged, never printed. It is not a test
fixture either; an attack found here enters a test corpus only rewritten by
hand, with every third party's name and host replaced.

A second feed is a second class beside `Moltbook` in `feed.py` with the same
`documents` method.
