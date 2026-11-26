---
class: design
subject: how Nora reads Instagram profiles, stories and posts, records opted-in members and answers comment mentions
owns:
  - instagram reader: the read-only reader for profiles, stories, post lists and individual posts through the instagram bridge
  - recorded social stream: the background observation of listed, opted-in members' own posts and stories
area: social
links:
  - {to: docs/design/social/README.md, rel: uses}
  - {to: docs/design/platform/instagram.md, rel: uses}
  - {to: docs/design/cognition/observation.md, rel: uses}
---

# Instagram

## Provider and supported surfaces

Everything on Instagram crosses the [instagram bridge](../platform/instagram.md);
there is no second integration to fall back to. The reader serves profiles,
stories, post lists and individual posts, plus an account search that returns
handles for a later read.

## Link and subject resolution

A spoken name resolves through the asking room and the person index, a typed
handle through the bridge's exact account lookup, and a pasted link by the kind
of item it names: post, reel, story or profile. A mobile page the reader needs in
canonical form can say so. Messaging and social identity are different id spaces,
mapped only by the person index.

## Read and search

Interactive reads answer a person's request and go only to the bridge. Search
returns handles and reads no content. No unavailable read is replaced by
another source or a different question.

## Account, audience and authority

- **Stories** open only when asked, and only for a listed person in the asking
  room. A room open to all persons or a direct-message grant does not count as
  listing a person, and every refusal returns one identical sentence whatever its
  reason. Handle and link forms check the exact account named and then the same
  roster rule, so a story link cannot be an oracle for membership in another
  room.
- A profile does not inherit that reach rule, but a shared post or reel still
  needs an asked turn.
- The recorded stream polls only listed members who have not opted out and
  never crawls tagged strangers. Its existence grants no live lookup to
  reflection or proactive passes.

## Results, media and custody

Post and story media is read through the common media path; captions never
enter the vision instruction. Each item reports seen, withheld, failed, timed
out or not read, so a true caption never stands for unseen content. Work is
bounded per item and per turn, and cancellation stops downloads, model calls
and scratch writes.

A member sharing an item into a room is room speech: the transcript keeps its
kind, author and a sanitised canonical link, with an excerpt of a long caption,
and the full caption stays in the source record. The media bundle owns the rule
that a linked post suppresses the redundant cover read. A page link cannot be
diverted through a generic fetch to dodge these rules.

Interactive reads are not cached with room media: bytes are read, rendered and
dropped, and a story read is never persisted to make a later question cheaper.
The recorded stream has its own lifecycle: posts and reels are kept whole,
story metadata, captions and perception text are kept as items, story bytes
expire, and opted-out, gone or hidden items drop out of later reads. A
perishable observation keeps its source kind and time: what a story showed at a
moment is not an enduring claim about a life, and no storage bound authorises
correlation across sources.

## Public summons

A listed person can mention Nora in a comment under a post. The bridge
recognises the resulting direct-message card, resolves its post and emits a
summons event independently of room ingest; a malformed or unresolved card
causes no model call and no reply. The occasion follows the
[shared summons rules](README.md#public-summons). Reading the post, its media
and the comment thread is bounded, with unread items marked and never described
from their caption. The writer additionally requires that the summoner wrote the
target comment mentioning Nora and that no earlier reply exists; the only check
that reads Instagram runs after every local check passes, and the claim
transaction re-runs the ledger checks. A safe staged failure may retry only the
flush under the same claim; a checkpoint or other failure that needs a person
stops further reads and writes.

## Failure and coverage

A failed profile is not an empty profile, a failed story read is not no story,
and an unreachable bridge is not evidence of absence. Derived claims keep their
source, time and perishability.
