# Self-awareness

Nora's identity is authored, but her current situation is generated from live
policy, adapters, records and registries. She can answer where she is and what
is around her without maintaining a second, silently stale description in
doctrine.

**Status:** built

[Open issues](https://github.com/mikeng-io/nora/issues?q=is%3Aopen+%22design%2Fself-awareness%2F%22)

## Parts

- **Authored identity** says what Nora is. It does not enumerate current rooms,
  handles, health, members or capabilities.
- **Generated presence** resolves the current room, the other rooms in its
  unit, transport state and her public handles at turn time.
  [Presence](presence.md) defines its scope and failure behavior.
- **The room directory** shares that resolution with the room tools so prompt
  and tool cannot disagree about which rooms exist.
- **Room listing and reading** answer activity and content queries at different
  disclosure levels. [Rooms](rooms.md) defines their unit boundary, activity,
  timestamps and pagination.
- **The tool catalogue** remains the capability layer. There is no second
  generated list of what Nora can do.

## Terms and promises

Presence states a situation, not an ability or a topic to recite. The current
unit's rooms may be named; other units are acknowledged only as existing.
Their names, members, activity and content do not cross into this room.

Room content and room existence are different disclosure tiers. Listing returns
no member names or message text. Reading returns other people's words as
untrusted content under the same scope as the current transcript tools.

Public handles are display text, not identity keys. A handle read from an
adapter wins over a declared fallback when available, but it never joins
sender matching or self aliases. A missing handle is omitted rather than
invented.

## Change and failure

Rooms and trust flags move through validated live policy. Health is read at
turn time, not cached into a claim of availability. An unavailable adapter,
roster, room directory or record read is `unavailable`, never zero members,
zero activity, a healthy transport or an empty history.

A relay into a destination that is not up refuses before fire or delivery and
returns an honest note. A reconnecting state may heal; a degraded or unknown
state carries no such promise. No prompt wording is allowed to turn a failed
send into a claimed one.
