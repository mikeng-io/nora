# Social reads, media and custody

## Resolution before dispatch

The social router is the single choke point for network reads. A spoken name
resolves through the asking room and the person index; a typed handle resolves
through the bridge's exact account lookup; a pasted link is parsed into the
kind of item it names. The model never supplies or sees a platform account id.

Resolution and authorization are different. Resolution says which account or
item the input names. Authorization says whether this read may happen in this
turn and room. Multiple matching people or accounts are ambiguous, no mapping
is nothing found, and an unreadable roster or index is unavailable.

Interactive profiles, stories, post lists and individual posts go only to the
bridge. Account search returns handles for a later read but does not read those
accounts' content. There is no fallback to another integration and no silent
substitution of a different question when a read fails.

## Story and shared-content scope

Stories open only when a person asks, and only for a listed person in the
asking room; a `persons = "*"` room or a `[dm] persons` grant does not count
as listing a person, and a refused read returns one identical sentence
whatever the reason. Handle and link forms check the exact account named, then
apply the same roster rule, so a story link cannot become an oracle for
membership in another room. A profile does not inherit the story's reach rule,
but shared posts and reels still require an asked turn.

Background social observation is separate from interactive reads. It polls
only listed members who have not opted out, records their items and perceptions,
and never crawls tagged strangers. Live social lookup verbs are not silently
granted to Cognition merely because the recorded stream exists.

A social read returns typed, curated views and one renderer. Captions, bios and
other stranger prose are wrapped as untrusted content; identifiers and raw
upstream fields are excluded. Media URLs are accepted only for known CDN hosts
and remain machine inputs for the existing media reader, not evidence of what
the media shows.

Posts and stories can be read with their media through the same media path used
elsewhere. Captions never enter the vision instruction. Each item reports seen,
withheld, failed, timed out or deliberately not read, so a true caption never
becomes proof of unseen visual content. Reading work is bounded per item and per
turn, and cancellation stops downloads, model calls and scratch writes.

## Shared posts and stories in a room

A member sharing an item into a room is room speech. The transcript preserves
the kind, author and a sanitized canonical link, with an excerpt of any long
caption. The full caption remains in the source record and returns with the
post read. A page link cannot be diverted through a generic web or media fetch
to bypass these rules.

A linked post or reel does not also offer its cover as a media handle. The
cover answers only what one still image shows and would make a successful but
incomplete read replace the actual post. A story keeps its cover because a
story link reads the author's current story, not necessarily the frame that
was shared. An invalid or missing link leaves the cover as the only available
content.

## Custody and memory

Room speech follows ordinary durable custody. Interactive social reads do not
enter the content-addressed room cache; their bytes are read, rendered and
dropped. Story reads and their fresh perceptions are not persisted merely to
make a later question cheaper.

The recorded social stream has its own explicit lifecycle. Members' posts and
reels are retained whole. Story metadata, captions and perception text can be
retained as Record, while story media bytes expire. Opt-out, gone and hidden
items are excluded from later reads and projections.

Perishable observations keep their source kind and time. A claim that a story
showed something at a particular time is not the same as an enduring assertion
about the person's life. Storage and phrasing bounds do not authorize broader
cross-source correlation; every derived claim still needs its own evidence and
read scope.
