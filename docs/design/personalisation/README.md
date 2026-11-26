# Personalisation

Personalisation is one engine with three jobs: fit ordinary replies to the
people present, rank outside options for a group, and decide whether an
unsolicited suggestion is worth making. It reuses profiles, Cognition and the
speaking path rather than creating another preference model.

**Status:** designed

[Open issues](https://github.com/mikeng-io/nora/issues?q=is%3Aopen+%22design%2Fpersonalisation%2F%22)

## Parts

- **Likes** are a read-time projection of eligible profile claims and their
  original evidence, including explicit constraints and uncertainty.
  [Likes](likes.md) defines evidence, destination scope and origin estimates.
- **Personalised replies** use the existing profile and likes views to lead,
  omit or check appropriately without announcing a person's private traits.
- **The ranker** generates, filters, scores, aggregates, diversifies and
  explains candidates under a frozen frame. [Ranker](ranker.md) defines its
  deterministic and model responsibilities.
- **Suggestions** use the existing Cognition and fire paths, an absolute cap
  and outcomes linked to the original decision. [Feedback](feedback.md)
  defines what counts as a pick and what teaches the proposal policy.

## Terms and promises

A like is not a hard constraint, an absence of evidence is not neutrality, and
a group choice is not every attendee's preference. Profile writers own what
Nora believes; the ranker never writes its scores back as evidence of taste.

Destination audience is fixed before any trigger, facet, candidate, score or
explanation. Every used and cited input must pass its disclosure, lineage,
exclusion and location requirements. Withheld evidence cannot affect a
shortlist even when the reason is hidden.

The model extracts facets and judges suitability where computation is not
possible. Code computes evidence support, constraints, logistics, group
aggregation, shortlist choice and the reasons recorded for replay.

## Change and failure

Recommendation types supply a closed facet vocabulary, constraints, candidate
sources, feature weights, triggers and proposal caps. A new domain needs no
new engine, but it does need its own grounded candidates and held-out evidence.

Unknown inputs stay unknown and important unknowns prevent a confident top
rank. Unreadable history makes replay unavailable, not a pass. Asking for a
suggestion bypasses sampling but never the cap on unsolicited proposals,
disclosure gates or action permissions.
