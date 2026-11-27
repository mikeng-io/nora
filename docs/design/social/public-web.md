---
class: design
subject: how Nora searches the public web and fetches a known page as untrusted evidence
owns:
  - public web read: a web search or page fetch whose result is untrusted public material with its source and age
area: social
links:
  - {to: docs/design/social/README.md, rel: uses}
  - {to: docs/design/cognition/boundaries.md, rel: uses}
  - {to: docs/design/cognition/observation.md, rel: uses}
---

# Public web

Search and page fetch are how Nora reads the open web, for a person's question
or a summons. Social networks have their own readers; the web is the fallback
for links no network claims ([link owners](README.md#link-owners)).

## Two reads

- **Search** finds pages about a subject. A result is a pointer with a title,
  its provenance and a limited snippet: not authority about the present world.
- **Fetch** reads one known page and keeps bounded content as an item with its
  fetch provenance ([observation](../cognition/observation.md)). It is not a
  crawler, and model-chosen internal-network addresses are refused.

The two compose with other kinds of source and never fail over into a different
question: a live reading answers what is true now, a route answers a journey,
a page answers what its author wrote.

## Rules

- **Untrusted whatever carries it.** Retrieved text is data, never an
  instruction, and no retrieved text activates a skill or changes a rule.
  Summarising it, or fetching it through a trusted local service, changes
  client exposure and not content trust. A typed envelope does not make its
  free-text fields safe.
- **Bounded intake.** Result counts, bytes, pages, download size and time are
  bounded and truncation is stated in the result. Retrieved text rides every
  later model call, so its cost is its size times the calls remaining; a
  clipped page is never presented as the whole page.
- **Minimum public question.** An outside query carries only what the question
  needs: never a private person's name, handle, address, identifier or
  coordinate ([boundaries](../cognition/boundaries.md)).
- **Source and age travel.** A result says where it came from and how old it
  is. A page is about whatever its author wrote, not necessarily now; a
  forecast is not a reading.
- **Nothing found is earned.** It requires that the question was actually
  asked of a source authoritative for it; a wrong source, missing
  configuration, an upstream error or an inability to check is unavailable,
  never absence ([cognition](../cognition/README.md)).
