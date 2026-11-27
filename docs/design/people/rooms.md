---
class: design
subject: the topology of unit, room and conversation, and how a room is resolved, addressed and listed
owns:
  - topology
  - room: "one conversation on one platform within a unit, with its own transcript and culture, keyed on platform and primary conversation id"
  - conversation id: "one platform thread identifier, what arrived, which a platform may change; a room may hold several and a conversation id belongs to at most one room"
  - room name
  - room resolution and relay
  - room directory
  - unit: "the boundary of what is read and shared by a group of people reachable in more than one room; rooms of one unit share state, rooms of different units never do, and one unit is primary while the rest are affiliates"
  - workspace: "a group of units under one operator, an authority scope only and never a read boundary"
  - home room: "a unit's one designated room, through whose roster a unit-wide room-scoped read resolves"
  - conversation: "one platform chat admitted as a room, holding one or more conversation ids of which one is the stable primary"
area: people
links:
  - {to: docs/design/people/authority.md, rel: uses}
  - {to: docs/design/cognition/boundaries.md, rel: uses}
  - {to: docs/design/cognition/graph.md, rel: uses}
---

# Rooms

## Topology

```
workspace        a group of units under one operator: an authority scope only
└─ unit          a group of people: the read boundary
   └─ room       one conversation those people use: its transcript, its culture
      └─ conversation id   one platform thread id; a room may hold several

platform adapter   transport for a network: receives, sends, reports health.
                   It owns nothing about people and is never an address.
```

| level | is | keyed on |
|---|---|---|
| unit | the boundary of what is read and shared; one is primary, the rest are affiliates ([graph](../cognition/graph.md)) | its name |
| room | one conversation, on one platform | platform and primary conversation id |
| conversation id | what arrived; a platform may change it (a group upgrade) | platform and id |

- A conversation id belongs to at most one room. A room may carry the old and new
  ids of a migrated chat, so the room does not go silent.
- **Room names are unique within a unit**, after case and spacing are folded. Every
  admitted room has a name, and a name never equals a network's name when that
  would make the network word resolve to one room by name rather than ask.
- A unit holds as many rooms as its people use, on any platforms, including several
  on one platform. Shared state across its rooms is the unit's own, rendered through
  the guards ([boundaries](../cognition/boundaries.md)); a room's transcript is never
  fed into another room.
- A unit has at most one home room.
- **A direct message is a room** whose audience is one person
  ([direct messages](direct-messages.md)). A **voice channel is a surface of a
  room**, not a room ([voice](../voice/README.md)).
- **A Discord server entry** stands for every text, announcement and forum channel of
  that server, each an ordinary room of the entry's unit, named by its channel.
  Ignored channels are never admitted; the rest are mention-only unless named
  ambient, an operator opt-in like any room's. A collision of channel names fails
  closed for that channel. A thread, including a forum post, is never a room: its
  messages belong to the parent channel's room and carry the thread's id, so a reply
  goes back into it.

## Room resolution

A message resolves once, at arrival: its platform and conversation id name its
room, and the room names its unit. An id no room carries is not a room of any unit,
and nothing infers one.

## Addressing and relay

Sending to another room names **the room**. The name resolves over this unit's rooms
only:

1. a room whose name matches;
2. else a network, when exactly one room of this unit is on it;
3. else, for a network with several of this unit's rooms, the answer is **ambiguous**;
4. else unknown, the same refusal for no such name and for another unit's room, so
   the answer reveals nothing.

- **She chooses from what each room is for.** The room list gives each room its
  operator description, the first line of its culture where that account is cited,
  and its recent topics, as data about the room, never as instructions. Which rooms
  exist and are reachable stays code's; which one fits is her judgement.
- **When two fit, she asks.** Ambiguity returns the unit's own room names, which the
  list already shows, and she asks the person which. She also asks when the list
  alone leaves two plausible.
- **The same room is decided by identity**, not by network, so a relay between two
  rooms on one platform is allowed and a relay to its own source room is not.
- **The trust boundary does not move.** The sender must resolve to an identity, both
  rooms must share a unit, the asker must be admitted in the destination and the
  text passes the egress guards. Choosing among the unit's rooms changes which
  admitted room receives it, never whether a room is admitted. The reply names the
  room it went to, so a wrong pick is visible at once.
- A relay into a destination that is not up refuses before it fires. A reconnecting
  transport may recover; a degraded or unknown one carries no such promise. No
  wording turns a failed send into a claimed one.

## Room directory

One directory resolves the current room, the unit's other rooms and each
transport's health, and feeds both the situation shown to a pass and the room tools,
so the prompt and the tools cannot disagree about which rooms exist.

- **Situation is generated, not authored.** It names the current room, the unit's
  other rooms with their transport state, and that other groups exist. It never
  gives room ids, another unit's label or another room's members. It does not
  volunteer itself as a topic and states no capabilities; the tool catalogue is the
  capability list.
- **Health is read when she speaks**, not cached. Up, reconnecting, needs a person,
  not running and unknown are distinct; an exception while reading health is
  unknown, never up. An unreadable directory, roster or record is unavailable,
  never zero members, zero activity or an empty history.
- **Listing and reading are two disclosure levels.** Listing returns each room's
  name, network, transport state, roster count where readable, and human activity,
  with no message text or member names. Reading returns what people said in one of
  this unit's rooms, as other people's words under the same scope as the current
  transcript. Another unit's room and an unknown one read the same.
- Activity comes from the record, not from a capped transcript, and counts human
  rows and distinct resolved speakers over the window, excluding Nora; a message
  count is never presented as a speaker count.
- Transcript times use the room's readable calendar and age vocabulary, never raw
  epoch values. A full page says whether older records exist, a cursor excludes its
  own point and never splits a same-time group, and an empty earlier page means
  nothing is recorded before that point.
