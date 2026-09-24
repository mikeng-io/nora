---
name: note-merge
kind: objective
version: 1
applies_to: [note_merge_adaptor]
description: >-
  The note tier's back-stop for duplicates (design/dreamer-agent-loop/41 §7,
  DEC-202). Reads every open note of one unit side by side and names the ones
  that are the same open question. Tools-less and one sample; code refuses
  what it can decide (an unknown handle, a group of one, a note in two groups,
  a group spanning rooms, a group with nothing changed since the last read)
  and, in shadow mode, writes nothing to the notes, only one row per fold it
  would have made, for the operator to label. Version 1 (2026-09-25) frames
  what the model is reading rather than listing cases; the eval in 41 §7.8 is
  what decides whether it is good enough for `on`.
change_policy: operator-only
---
What you are reading is one group's notebook of **open questions**: things the group has
not settled yet, written down so they survive between conversations. `<today>` is today, in
the group's own day. Each `<open_note>` is one entry as it stands now: what it is about, what
is still undecided, what would settle it, and the body written so far — its first part, when
it is long. Each entry says which room it was born in, how long ago it was written and last
added to, and, when it turns on a day, that day and how far it is from today.

Entries are written by different passes at different times, in whatever words were at hand,
so a question the group is still deciding can end up written down twice, under two titles.
Entries marked `changed` were written or added to since this notebook was last read this way;
the rest are as they were then.

Say which entries are **the same open question**: the same thing the group has not settled,
so that whatever settles one settles the other. Sharing the people, the day or the place is
what separate arrangements usually have in common, and it does not make them one question.

When you cannot tell, leave them apart. Two entries left apart cost one extra line in the
notebook, which anyone can see. Two different questions folded into one entry cost the group
a question it was still deciding, inside an entry that reads as if it were one.

## Answering

Return **one JSON object** with a single key `groups`. Each group lists the `id`s, exactly as
given, of entries that are one open question, and `why` — one sentence, in the room's own
words, saying what that one question is:

```json
{"groups": [{"notes": ["2", "5"], "why": "both ask which restaurant Saturday's dinner is at"}]}
```

An entry belongs to at most one group. If no two entries are the same question — the usual
answer — respond with exactly `{"groups": []}`. Nothing outside the single JSON object.
