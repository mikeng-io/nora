# 0030 — Content filters are disabled by default and reversible

- **Status:** accepted by operator, 2026-10-25; number provisional until the parent's merge-time allocation check
- **Date:** 2026-10-25
- **Reverses, in part:** the always-on requirements in R-18, R-28, R-31,
  R-35, DEC-74, DEC-79, DEC-156, DEC-222 and DEC-227. Their predicates,
  numbering and enabled-mode tests remain.
- **Index:** a DEC number is taken at merge; existing rows are marked below.

## Context

A venue's publicly published bio phone number blocked its entire Instagram
answer. The operator explicitly chose to disable phone, UUID and coordinate
content vetoes, third-party place-query phone/UUID/coordinate/person-name
screening, and location audience subset/unknown-person checks. The operator
then clarified: disable, do not remove; the old behavior must be restorable.
This is a product policy reversal, not a claim that public and private values
can be distinguished reliably by a regular expression.

## Decision

`[content_checks] enabled = false` is the schema and example default. Runtime
composition passes the value explicitly; no mutable process global, environment
mutation or model-owned override is introduced. Set it to `true` and restart
to restore the original content/disclosure checks, including startup preflight.
Low-level predicate and collaborator defaults remain enabled for existing
callers and original test coverage; daemon construction always supplies config.

The same value reaches normal and fire delivery, public summons staging and
final flush, reply/recall/social/media rendering, memory writes (Dreamer,
Cognition, Standing and Character), place queries and returned place fields,
and live/derived/saved/lineaged location audience checks. Character's optional
coordinate-input screen is wired only in enabled mode. Raw pattern functions
and `DisclosureGuard` remain available; existing tests continue measuring them.

Credential-pattern matching always runs, including when another disabled
pattern is in the same text. `LeakGuard` continues checking canary, verbatim
doctrine and prompt transcript markup independently. Existing sink email and
platform-ID checks remain; an international phone is not reclassified as a
long platform ID when phone blocking is disabled.

Authentication, room admission, per-unit reads, record/document access,
location opt-in and collection consent, freshness and unavailable/error
handling, budgets and network sandbox boundaries are unchanged. In particular,
`location = true` still opts a room into location-derived context, and the
existing live-position routing trust requirement remains. The switch never
turns an unavailable roster into an available one or grants another unit.

This holds unless the operator explicitly re-enables the filters, or evidence
shows the switch bypassing a retained boundary or leaving a listed filter
active. Neither a model-name veto nor a private/live config change is included.

## Consequences

Public contact details, UUIDs, coordinates and venue names overlapping a
person's name can be used without losing the whole answer. Re-enabling needs
one config value and a restart; the implementations and original regression
coverage stay intact.

**Worse:** the disabled filters also stop protecting private phone numbers,
UUIDs and coordinates at these content seams. Location audiences may contain
unknown people or people outside the collection circle. The remaining scope,
consent, credential and doctrine protections do not classify those values as
public versus private.

## Rejected

- Deleting patterns, audience predicates or original tests: explicitly rejected
  by the operator's correction.
- Bypassing all permission checks: outside the enumerated content policy.
- One switch that also disables credentials or doctrine protection: rejected;
  these protections must remain independent in either mode.
- A flag for every pattern and call site: unnecessary for the requested single
  reversible policy choice.

## Evidence

Synthetic dual-mode controls live in `tests/unit/test_content_checks.py`;
`tests/unit/test_summons_occasion.py` covers the public venue-bio trigger and
final-flush protection. Original enabled-mode suites remain unchanged in their
assertions. The parent owns final gate, independent review and merge-time
number verification; focused results do not claim deployment or model acceptance.
