# Instagram adoption

Instagram joins Nora as two related but distinct surfaces: group messaging and
a social network. One bridge owns the Instagram session and every Instagram
read or write, while separate messaging and social contracts prevent those
capabilities from leaking into one another.

**Status:** built

[Open issues](https://github.com/mikeng-io/nora/issues?q=is%3Aopen+%22design%2Finstagram-adoption%2F%22)

## Parts

- **The bridge** is the only door to Instagram. It owns authentication,
  realtime connection, upstream calls, thread and account translation, media
  normalization and write guards.
- **The messaging adapter** turns bridge events into Nora's ordinary rooms,
  messages, mentions, replies, reactions and attachments. Outbound text and
  media remain separate acts on this transport.
- **The social router** resolves people, handles and links, applies the read's
  scope and dispatches to the bridge. The model names a network and a subject,
  never a backend or an account id.
- **Social reads and the recorded stream** cover profiles, stories, post lists
  and individual posts. Interactive reads answer a person's request; the
  background stream observes only listed, opted-in members.
- **Media custody and readings** distinguish content sent to a room from
  content Nora fetched. Shared links point to the item itself rather than
  offering a cover image as a cheaper but incomplete answer.
- **Public summons** are a separate comment occasion with no room or memory.
  A listed person's mention is read with its post and comment thread before
  one guarded, idempotent reply can be published.

The subjects are [bridge and messaging](bridge.md), [social reads and
custody](social.md), and [public summons](summons.md).

## Invariants

- Every Instagram capability crosses the bridge. There is no second client,
  credential or fallback integration whose failure could be mistaken for a
  different answer.
- Messaging identity and social identity are different id spaces. The person
  index maps them; the bridge and typed boundaries never guess from decimal
  strings.
- A shared session does not imply a shared port. The messaging adapter cannot
  fetch a profile and the social provider cannot send a room message.
- Scope is decided before dispatch. Unknown, ambiguous or unreadable identity
  fails closed, and an unavailable backend is not replaced by another read.
- No platform identifier, credential, signed private URL or raw upstream body
  reaches the model. Curated views carry only the values the read needs.
- Room speech and social reads have different custody. Derived claims retain
  source, time and perishability rather than becoming context-free facts.
- Public comments are not room sends. They use their own admission, staging,
  publication and ambiguous-outcome rules.
