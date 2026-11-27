---
class: design
subject: how a platform message becomes one typed value and how an edit or quote is recorded and shown
owns:
  - normalized message: the one typed value every platform's inbound message becomes, and the only form the app reads
  - edit target: the reference by which an edit names the message it edits
  - newly addresses rule: only an edit that newly addresses Nora opens a turn
  - resolved quote: quoted text shown to the model is the stored parent's text, or nothing
area: conversation
links:
  - {to: docs/design/cognition/observation.md, rel: uses}
  - {to: docs/design/cognition/context.md, rel: uses}
  - {to: docs/design/platform/README.md, rel: uses}
---

# Conversation

A conversation is the stream of what people say in a room. Every message is
captured as an [item](../cognition/observation.md) before any judgement, and the
transcript a pass sees is a view over those items, rendered by context
assembly. This bundle owns the message value, how an edit relates to its
original, and how a quote is shown.

## The normalized message

Each platform's parser produces one **normalized message** and the app never
reads the raw text again ([platform](../platform/README.md)). It carries:

- the platform, account, room and sender as the platform states them;
- the body, with mentions as structure rather than text, and media as handles;
- the quote facts the sender asserted, and the platform handle of the message;
- for an edit, an **edit target**;
- the raw envelope, kept as provenance.

A mention is structural: whether Nora is addressed is read from the mention
list and reply relationships, never from a substring.

## Quotes

A quote is a claim by the sender, verified against nothing: anyone can build a
quote that attributes any words to anyone. The rule is that **quoted text shown
to the model is the resolved parent's stored text and author, or nothing**. The
quote resolves to the message Nora recorded; if it does not resolve, the
transcript shows the reply arrow with at most the author, or omits the block.
The envelope's copy is never shown. An unknown older parent keeps the
relationship and invents no timestamp or text.

## Edits

An edit is one typed event on every platform. It is recorded as an item beside
the message it edits, never over it: the original is append-only. Reads fold the
latest edit onto its original, so the transcript shows the latest text at the
**original position**, with when it last changed and what it first said. The
position never moves, so replies that point at the message keep resolving. An
edit reaches cognition as a correction of its target, never as a second
statement, and claims that rested on the earlier text are due for
reconsideration ([graph](../cognition/graph.md)).

### How an edit finds its original

An edit names its target by the platform's handle for the message or, on a
platform that addresses messages by author and sent time, by that pair. A later
edit may name the previous revision, and a reply to a revision is resolved back
to the original. The edit's own envelope serves its text; the original's
serves handles, because a reply or a reaction must name the original.

### What happens to an edit

| outcome | when | recorded |
|---|---|---|
| recorded | folded onto its original | the edit item |
| engaged | recorded, and it newly addresses Nora | the edit item and a turn |
| unchanged | text and mentions equal the version it replaces | only when newer, because a chain's next edit names it |
| stale | older than the newest edit on record, or already recorded | nothing |
| orphaned | no original on record | nothing |
| refused | the original is Nora's own, or its sender differs from the editor | nothing |

- **Newly addresses.** An edit opens a turn only if the engagement rule would
  engage the edited message and would not have engaged the version it replaced.
  Adding a mention of Nora to an answered message opens a turn; fixing a typo in
  a message that already addressed her does not; an edit to a reply to her
  does not; and a version whose earlier mentions are unknown opens none, as a
  missed turn is safer than a second answer. Whether the earlier version replied
  to Nora is read from the recorded message, never from the edit event, which may
  not carry the quote.
- **Orphans are dropped.** An old message landing at the bottom of the
  transcript as if said now, or duplicated by a race, costs more than losing a
  rare genuine edit of an unrecorded message.
- **Identity comes from the original** when the platform's edit names no
  sender, and must match it when it does: an edit is not a way to put words
  under someone else's message.
- **A platform update that is not an edit** (a preview resolving, a pin) is
  recognised by having no edit time and is dropped. Filtering on the text and
  the clock rather than event types keeps this total across platforms and
  redeliveries.
- Edits are ordered by arrival among edits of one message, except that an edit
  naming the latest revision is newer whatever its device's clock says.
- An edit is not a new utterance: counts of utterances, exchanges and speaker
  turns do not change, and it is never a candidate for an ambient offer.
- Mentions are a change in their own right, and an edit that only changes
  attachments keeps the original's handles.

### What the model sees

An edited message renders with its text, the age of its last edit and what it
first said. The age is there because the judgement it feeds is about time: was
her answer before or after the correction? The first text is there so the model
can see what her earlier reply was answering. Only the first text is shown, not
the chain.

## Not in scope

Nora does not re-answer a question she already answered when it is later
edited; the corrected text is in front of her on her next turn. Deleting or
unsending is a separate typed event ([observations](../platform/observations.md)),
and Nora editing her own message is an act.

## Why this shape

- **A side record, not a second message**: every counter, window and measure
  that reads messages would otherwise count an edit as a new utterance.
- **Fail toward silence**: when an edit cannot be tied to its original or its
  earlier mentions are unknown, the safe outcome is a missed turn.
- **Resolved quotes**: a quote is only a claim by the quoting sender, so the
  stored message is the evidence.
