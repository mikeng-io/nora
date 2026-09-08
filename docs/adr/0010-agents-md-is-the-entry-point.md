# 0010 — AGENTS.md is the entry point; CLAUDE.md is a pointer

- **Status:** accepted
- **Date:** 2026-09-08
- **Index:** none — this is a governance decision about the repo's own
  documentation, not about Nora's runtime behaviour, so it earns no DEC row.

## Context

The repo carried its full agent rule set (404 lines: the comprehend → measure →
serialize spine, the reporting registers, the "Never" list, the instrument
rules, the five operational constraints) in `CLAUDE.md`, with `AGENTS.md` as a
seven-line file whose only job was to say "read `CLAUDE.md` instead".

That put the authoritative rules in a **vendor-specific** file. `CLAUDE.md` is
read by Claude Code; `AGENTS.md` is the convention a growing set of coding
agents look for ([agents.md](https://agents.md)), and this repo is already
worked on by more than one of them — Codex and Kimi have both been used as
external reviewers here. An agent that reads only `AGENTS.md` got a redirect
and, depending on how it follows one, possibly nothing.

The alternative shape is what the sibling `trustless` repo does: keep
`CLAUDE.md` as "authority and intent" and let `AGENTS.md` carry workflow
shortcuts. That is a **two-file split with two kinds of content**, and it works
there. It was rejected here for the reason this repo already states about
`AGENTS.md` in its own words: *"so tools that specifically look for `AGENTS.md`
find the same rules instead of a second, drifting copy"*. The split invites the
drift; the pointer forecloses it.

## Decision

`AGENTS.md` carries the rules. `CLAUDE.md` is a pointer at `AGENTS.md` — the
same shape `AGENTS.md` had before, reversed.

`tests/test_architecture_doc.py` pins both halves: the operational-constraint
phrases must appear in `AGENTS.md`, and `CLAUDE.md` must stay under 30 lines
and name `AGENTS.md`. Without that second assertion the pointer is free to
grow back into a copy, which is the exact failure this decision exists to
prevent.

## Consequences

**What this buys.** One authoritative file, reachable by any agent that follows
either convention. A vendor-neutral entry point, so adding a third tool costs
nothing. A test that makes the copy-drift failure mode loud instead of silent.

**What got worse.** 214 files in this repo name `CLAUDE.md` — production
docstrings, migrations, `Makefile`, `docker-compose.yml`, most of `design/`.
Every one of them now resolves to a pointer rather than to the rule it meant to
cite: one extra hop for a human, and for an agent that reads the cited file and
stops, a redirect it must follow. Sweeping all 214 was rejected as a worse trade
— a 214-file diff that touches migrations and production code to fix a doc
reference is a review burden with real regression surface, for a link that still
resolves. Only the top-level documents were updated; the in-code references are
a follow-up issue, and until it lands the repo is inconsistent about which name
it cites.

**What is now load-bearing that was not.** The `CLAUDE.md` pointer is
infrastructure, not a courtesy file. Deleting it silently removes Claude Code's
entry point, and the only thing that would catch it is
`test_claude_md_is_a_pointer_and_not_a_second_copy`.
