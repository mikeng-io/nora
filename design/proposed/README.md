# Proposed — NOT the architecture

Everything in this directory describes something that **does not exist yet**.

It was separated out on 2026-08-06 because `design/` had accumulated eighteen
documents in four different states — `current`, `proposed`, `implemented`,
`open-decision` — several of which contradicted each other, and a reader could
not tell which described Nora and which described an intention.

**To learn what Nora actually is, read `ARCHITECTURE.md`.** To learn why she is
that way, read `design/09-decision-index.md`. Neither points here.

A document leaves this directory when the thing it describes is built, and it
is edited into `ARCHITECTURE.md` rather than promoted wholesale — a proposal
argues for a change, and once the change exists the argument is history, not
description.

| doc | proposes | state |
|---|---|---|
| `14-grounding.md` | four kinds of grounding: live transit data, routing, web search, page fetch | partly measured; `transit` is next to build |
| `15-gif-replies.md` | Giphy as a second reply verb | signal-cli attachments verified; nothing built |
| `26-media-replies-and-signal-stickers.md` | adapter boundary for Giphy delivery and Signal sticker reading | elaboration; nothing built |
| `16-multi-message-replies.md` | relaxing R-2 so one tag can send several messages | mechanism measured; nothing built |
| `18-context-budget-and-handles.md` | rationing fetched content per turn; message handles | **contested** — a review argued the budget is a component where a constant would do, and that the live `limit_total_tokens` counter should be read first. Its cheap half (the `↳` snippet) shipped in `624955d`; the rest is deferred. |
| `27-presence-analytics.md` | the DERIVED layer of the presence stack as a consumer-neutral substrate, plus the six-tier programme above it | **P0 spec written** (`docs/superpowers/specs/2026-08-18-presence-analytics-substrate-design.md`); Tiers 1–2 ready to build, Tier 4 blocked on 4–8 weeks of episodes, Tier 6 speculative |
| `28-relevance-classifier.md` | A3 true-ambient: single cheap flash completion on the ambient path deciding engage yes/no; off-path bounded queue; shadow mode first | **brief only** — nothing built; replaces `VoiceNotePolicy` for untagged voice notes when it lands |
| `29-image-generation.md` | provider-agnostic `ImageGenerator` port; first impl = xAI subscription lane via Nora's own OAuth (DEC-137) | **proposal only** — nothing built; ships OFF (`[image_gen] enabled = false`) |

Each carries corrections applied after an adversarial review on the day it was
written. Read the corrections — several are recorded as struck-through claims
with the reason, deliberately left visible so the same wrong turn is not taken
twice.
