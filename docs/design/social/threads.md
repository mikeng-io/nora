---
class: design
subject: how Nora is summoned and answers on Threads
owns:
  - threads surface: Threads as a public summons surface served through its own bridge, with a read-only reader beside it
area: social
links:
  - {to: docs/design/social/README.md, rel: uses}
  - {to: docs/design/cognition/boundaries.md, rel: uses}
---

# Threads

## Provider and supported surfaces

Threads is a public summons surface, not a room and not only a reader. A person
writes a mention in a reply under a post, Nora reads the post and the chain
above it, checks the claim from public sources or draws what was asked, and
answers in that thread. It follows the
[shared summons rules](README.md#public-summons); this file states only what is
particular to Threads.

A Threads bridge, with its own private network and no public listener, holds the
network token, so the process that reads injected content never holds a key that
can post publicly. Its read allowlist covers mentions, posts, conversations and
replies; its one write is the reply to a summons it served. A reader for a
Threads link a member pastes in a room is a read-only network reader beside it.

## Link and subject resolution

The subject is the summons itself: its author, text and the chain from root to
parent, each post with its author handle, text, media handles and age. An empty
question means the post above. The chain is clipped from the middle, keeping the
root and the nearest posts, and says how many it dropped.

## Read and search

Mentions are polled, because nothing in Nora receives inbound internet traffic.
The chain is rendered as untrusted content, the question as the summons text
without the mention, and every post with its age and which end is newest, since
a claim's truth depends on when it was said. Images in the chain are read by
handle when asked about, not at intake.

## Account, audience and authority

- A summoner is recognised by a Threads account bound in the identity mapping,
  matched by username because the platform exposes no author identifier. A
  username can change hands, so it is safe only while the platform's own list of
  permitted accounts also binds by account; before that gate goes, the username
  must be pinned to an account.
- A reply is at most 500 characters of plain text, and an over-long reply is
  sent back to be shortened and never truncated: a cut-off fact check is a wrong
  one. One reply per summons; text, text with one image or an image alone.
- Public replies carry a public-surface doctrine: the audience is strangers
  and the answer permanent, claims carry sources, the summons' language is used,
  and nothing is said about the summoner or anyone beyond the thread.

## Results, media and custody

The platform fetches an image by public URL, so a generated image needs a
short-lived public host; the post does not depend on the URL after publication.

## Public summons

See the [shared summons rules](README.md#public-summons).

## Failure and coverage

Reading the post under a summons needs the platform's advanced access: below it
a mention under a post its author is not a tester of arrives with the parent
withheld, and Nora stays silent instead of answering an unseen claim. Direct
quotes are not visible either, so what was quoted is unknown. A long-lived
token expires and cannot be refreshed once expired; its remaining life is
reported as a gauge so expiry is an alert, never a surprise.
