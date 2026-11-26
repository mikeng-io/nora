# WhatsApp adoption

WhatsApp joins Nora through a companion-device bridge and the same normalized
ingest, identity, memory, observation and delivery owners as every other
platform. Platform-specific code stops at transport, account identity and
native media semantics.

**Status:** built

[Open issues](https://github.com/mikeng-io/nora/issues?q=is%3Aopen+%22design%2Fwhatsapp-adoption%2F%22)

## Parts

- **The bridge process** holds the dedicated account's companion session,
  admits configured traffic and exposes authenticated service endpoints on the
  private deployment network. [Bridge](bridge.md) defines capture and delivery.
- **Identity and policy** normalize phone, LID, chat and mention forms without
  conflating them, then apply the same room and person admission as other
  platforms. [Identity](identity.md) defines that boundary.
- **Durable capture** normalizes each admitted transition once, writes the
  journal before queueing and replays in order after failure. A legacy SSE
  surface may remain compatibility plumbing, not the correctness authority.
- **Media and observations** preserve native attachments, polls, contacts,
  events and room changes under the shared custody and observation contracts.
  [Media](media.md) defines WhatsApp-specific custody and coverage.
- **Outbound actions** reserve, execute and read back text, reactions, edits
  and attachments with stable action identity and every confirmed native post
  reference.

## Terms and promises

The bot uses a dedicated account, never the operator's personal account. It is
conversational and reply-oriented: no bulk campaigns, broadcasts or outbound
automation to unknown people. A narrowly admitted direct-code flow is an
exception to that shape, not a general direct-message grant. Voice calls are
never answered; only messages are handled.

A phone number and a LID are different identifiers that may map to one person.
A display name is never an identity key. Unknown rooms and senders fail closed
before audit, media download or model work. A later validated policy edit
admits through the ordinary live-policy path.

The journal, not an in-memory buffer, is the capture commit point. Queue
failure does not erase it, replay does not duplicate it and ambiguity is
retained without a guessed transition. Delivery success comes only from
confirmed action state; an uncertain send is not retried into a duplicate.

## Change and failure

Protocol and dependency upgrades belong at the bridge boundary. Pairing and
session replacement are operational actions on the account credential, not
migrations of Nora's record. Process liveness, pairing, socket connectivity,
event freshness and queue health remain separate observations.

Unreadable media, undecryptable votes or edits, missing creation secrets,
clockless repeated transitions and partial room snapshots remain explicit
coverage gaps. They never become empty messages, zero votes, deleted members
or fabricated speech. No typing indicator becomes stored evidence or a wake.
