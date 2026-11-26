---
class: design
subject: how WhatsApp joins Nora through a companion-device bridge
owns:
  - whatsapp bridge: the service that holds the dedicated account's companion session and normalises its native events
area: platform
links:
  - {to: docs/design/platform/README.md, rel: uses}
  - {to: docs/design/platform/liveness.md, rel: uses}
  - {to: docs/design/platform/observations.md, rel: uses}
---

# WhatsApp

## Account and service boundary

The WhatsApp bridge is a small service speaking the companion-device protocol.
It publishes no public listener and uses the shared service credential. Only
its own session state and its capture stores are visible to it, never Nora's
database or model configuration.

Nora uses a **dedicated account**, never a person's own. It is conversational:
no campaigns, no broadcasts, no outbound automation to unknown people. Calls
are never answered. Pairing links the account and puts its credentials in the
bridge's session store; losing that store requires re-pairing and does not
touch Nora's record. A logout stops reconnecting and leaves health able to say
the account is unpaired. Credentials never reach logs, traces, fixtures or the
model. An official business integration would be a different account and trust
design, not a configuration of this bridge.

## Identity and admission inputs

- A phone address and a local identifier (LID) are different identity kinds
  that can name one account. The bridge keeps the mapping in both directions and
  carries whichever forms it knows for senders, mentions, quoted participants
  and Nora's own aliases; one is never formatted as the other.
- A person resolves through the identity mapping, never a display name. An
  unresolved raw address matches nobody and fails closed.
- A group's identity is the native group chat; a direct conversation uses the
  resolved phone chat when known and the raw LID chat otherwise. Addresses are
  normalised once before policy matching, so two spellings never make two rooms
  or a silent denial.
- Rooms are denied by default. Admission does not imply location, tool,
  public or proactive permission. An outbound direct address is limited to
  declared identity accounts, with the narrow exception of a requested one-time
  code under its own purpose and rate limits.
- A reply to an older unknown parent keeps the reply relationship and invents
  no parent text. A voice note's engagement follows the ordinary media policy,
  not a keyword in a transcript.

## Inbound events and durable capture

Admission comes first: a denied room produces no journal, media or queued work.
An admitted event is normalised once with its source scope, object and actor
references, source time and native ordering, then journaled; the journal append
is the commit point and waits for neither the queue, a model nor a receipt.
Replay publishes in order without overtaking an older entry or repeating a
confirmed one.

A native event with no clock or with a reused key is kept in a committed-state
ledger. A true repeat and a stale replay cannot always be told apart; an
ambiguous event is retained for diagnosis, neither queued nor folded. The
legacy event stream is only a compatibility surface with a bounded buffer that
reports overflow; it never establishes complete capture.

## Outbound effects and receipts

Each effect is reserved under a stable action identity before native work, and
a replay returns the recorded outcome. A confirmed action keeps every native
post handle in order for later edits, reactions and audit; an unconfirmed one
keeps none. A definite refusal is told from transport uncertainty, and only a
failure that cannot have taken effect may be retried. Text and attachments of
one reply share the action but remain separate native posts. A quote the
platform cannot carry degrades to an ordinary reply. Audio is claimed as a voice
bubble only when it was sent as one.

## Media and native observations

Attachments are downloaded into custody before the event is journaled. A voice
note's native flag can admit an engagement occasion even when transcription
fails, but the reply then says the content is unavailable instead of answering
an imagined transcript. Contact cards are reduced to bounded fields and no
shadow media archive is kept.

Reactions, edits, withdrawals, polls, contacts, scheduled events, pins,
membership and room metadata enter as typed events
([observations](observations.md)). A poll keeps the secret material needed to
read later votes within its room; a vote without it is unavailable, not a
guessed option. A text-bearing edit or service message keeps both the
delivered utterance and the target it concerns. Native clocks are kept per
field where they exist.

## Liveness and recovery

Process health, pairing, socket connection, event freshness, queue depth,
journal failure and action outcomes are separate readings. A logged-out or
half-paired account degrades WhatsApp alone, and re-pairing, done by a person,
recovers it without restarting the app ([liveness](liveness.md)). Typing never
becomes evidence or a wake.
