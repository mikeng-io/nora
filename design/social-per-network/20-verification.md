---
type: design
title: "Verification: what proves each PR, and what is still unmeasured"
description: "The tests that go red, the routing cases, the live read-backs after each deploy, and the four LinkedIn properties nobody has measured yet."
tags: [nora, social, x, linkedin, verification, evals]
timestamp: 2026-09-24
edges:
  - {dst: 10-changes, rel: depends_on, provenance: asserted}
---

# Verification

## Tests that must go red if the design is broken

| Property | Test |
|---|---|
| The four tools' vocabularies, the bridge allowlist and `read_only=True` agree, and no `instagram` or write verb is reachable | `test_r86_…` in `tests/test_acceptance_rules.py`, derived from `social_read_vocabulary()` |
| No read has two tools | the disjointness check on `social_read_vocabulary()` |
| `social_fetch` is gone from the registry, the Dreamer lists and every prompt | a registry test, and a grep of `nora/` for the name that expects no hit outside decision-history comments |
| no mutation spec targets a test that no longer exists | a check over `scripts/mutations/*.json` targets, run once in PR 1 |
| Every bridge allowlist value has a validator, and an unknown `(site, command)` spawns nothing | new, `tests/unit/test_social_bridge_server.py` |
| No LinkedIn profile argv is built without `--profile-url` | new, same file |
| A LinkedIn tool in a fire turn or a Dreamer pass is `refused` before any parse or call | new, modelled on `test_a_background_turn_is_refused_a_story_post_or_reel_before_any_call` |
| with `[social.linkedin] enabled` false, both LinkedIn tools answer `unavailable` and nothing reaches the bridge | new |
| the egress and coordinate screens run in both LinkedIn tools before any call | new, per tool |
| Each `_read` tool refuses another network's link with a note naming the right tool, and sends nothing | new, per tool |
| A LinkedIn post link is `unavailable`, not a read of the author's activity | new |
| The L1 opening paragraph, ADR 0015 Level 3 ceilings, a note on every non-`ok` status | the existing policy gates in `tests/test_tool_surface_policy.py` |

Per the repo's rule for a fix, each new test is shown red by reverting the
line it pins, in a committed state, before the PR claims it.

## Routing cases

Rows in `evals/corpora/tool_selection.yaml`, run in the resident arm, n ≥ 3:

| Message | Must call | Must not call |
|---|---|---|
| a pasted `x.com/<user>/status/<id>` link, "what's this" | `x_read` | `web_fetch`, `x_search` |
| "what has @<handle> been posting on X" | `x_read` | `x_search` |
| "search X for <topic>" | `x_search` | `web_search`, `x_read` |
| a pasted `linkedin.com/in/<id>/` link, "who's this" (PR 2) | `linkedin_read` | `web_fetch`, `linkedin_search` |
| a pasted `linkedin.com/company/<slug>/` link (PR 2) | `linkedin_read` | `web_fetch` |
| "find <name> on LinkedIn" (PR 2) | `linkedin_search` | `linkedin_read`, `web_search` |
| a pasted Instagram profile link | `instagram_read` | `x_read`, `linkedin_read` |
| a pasted news article link | `web_fetch` | every social tool |

Rows are added in the PR that lands the tool they expect, so the corpus never
names a tool that does not exist. Synthetic handles and ids only: the corpus
is committed, and no member of a room is named in it.

## Live read-back after each deploy

- **PR 1:** one `x_read` of a post link, one of a handle's posts, one
  `x_search`, each seen as an `execute_tool` span in Jaeger with the bridge's
  `opencli twitter …` argv in its log, and `social_fetch` absent from the
  tool list the model is sent (Langfuse, one turn).
- **PR 2:** the operator confirms private profile viewing on the account
  first. Then one of each LinkedIn read and one search, the same way, and the
  bridge restarted on norty from the pulled checkout.

## Resident cost, measured

**PR 1 (2026-09-24):** built the registry for a Signal group room and
cl100k-encoded `json.dumps` of each tool's `tool_spec` (05 §1's method).
Against master at `0014c663`: `social_fetch` was 454 tokens; `x_read` is 336
(description 163) and `x_search` is 188 (description 91). The resident total
moved **12,544 → 12,614 (+70)** for one more tool. Both descriptions are well
under the Level 3 ceiling of 320.

**PR 2 (2026-09-24), the same method,** against master at `1eaa94b7`:
`linkedin_read` is 348 tokens (description 155) and `linkedin_search` 196
(description 98). The resident total moved **12,614 → 13,158 (+544)**. The
tools are registered whether or not `[social.linkedin]` is on (10 §Config),
so a deployment with LinkedIn off pays this too.

## Unmeasured, and what would measure it

| Property | Why it matters | Reading |
|---|---|---|
| What OpenCLI returns when LinkedIn's monthly people-search limit is reached | decides whether it gets its own note | the first time it happens, from the bridge log (exit code and stderr) |
| Whether `media.licdn.com` image URLs load without LinkedIn's cookies | decides whether `media_fetch` can see a LinkedIn post's image | one `media_fetch(url=...)` on a URL from a live `posts` read |
| Whether `profile-read` ever fills location, experience or education | decides whether `experience` stays its own `what` | `profile-read` on 5 profiles of different kinds |
| How often LinkedIn challenges or restricts an automated dedicated account | the account is the whole backend, and a restriction takes LinkedIn dark | the bridge's `unavailable` rate for LinkedIn rows over the first weeks |

The probe in 00 is n=1 per command. It shows each command works, not how
reliably it works.
