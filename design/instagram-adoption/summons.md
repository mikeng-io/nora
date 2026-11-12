# Public summons in comments

## A separate occasion

A listed person can mention Nora in a comment under an Instagram post. The
bridge recognizes the resulting direct-message card, resolves its post and
emits a summons event independently of ordinary room ingest. An unresolved or
malformed card produces no model call and no reply.

The occasion constructs no room, transcript, roster, memory or audit history.
It holds the bridge, a summons ledger, the summoner identity index, public
instructions, public read-only tools and a staging deliverer. Room memories,
private profiles, stories and location reads are outside its grant.

The post and the surrounding comment thread are read before answering. The
summons comment is the one written by the card's sender that mentions Nora.
If the post, media or comment cannot be read, the occasion stays silent rather
than answering a claim it never saw.

## One answer, at most once

The ledger claims the card before any read and binds the discovered comment
once. Duplicate cards or an already answered comment cannot publish twice.
The model may research each public claim and compose a reply, or deliberately
remain silent. Prose without an explicit send is never published.

The reply first passes the ordinary content and disclosure guards into a
staging deliverer. Only the staged text can then flush through the bridge's
comment write. The target was fixed by the summons; the model chooses whether
and what to say, never where to publish.

The bridge independently verifies that comment writes are enabled, the session
is ready, a recent served summons exists, its sender wrote the target comment
mentioning Nora, no earlier reply exists, and rate and freshness guards pass.
These checks run in the writing process, not in model instructions. The only
guard that reads Instagram runs after every local guard has passed, so no
other refusal costs an upstream call on the shared session; the claim
transaction then re-runs every ledger guard and re-checks the author against
live summonses, so a window that closes during the read authorizes nothing.

Publication is claimed before the upstream call. An ambiguous result never
retries into a possible duplicate public comment. Explicitly safe staged
failures may retry only the flush under the same claim, while checkpointing or
other needs-human failure stops further reads and writes.

## Media and observation

Post items are pre-read through the same media mechanism as an interactive
post read, without injecting captions into vision instructions or persisting
the fetched media. Each unread item is marked honestly rather than described
from its caption.

Telemetry identifies the ledger row and outcome without exposing comment,
account, username or post identifiers. The model trace remains the evidence of
what the public turn actually read; it is not a second memory or room history.
