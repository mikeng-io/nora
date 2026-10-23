---
kind: design
title: "Nora — What version she is, and telling the group about it"
description: "The build-time version stamp (why not a GitHub call), the three-state read that keeps an unstamped build from claiming she has no versions, and the once-ever upgrade note routed through the existing fire path."
tags: [nora, design, version, release, announcement, r-42, dec-79]
timestamp: '2026-08-11'
---

> **Superseded in part, 2026-10-16 (DEC-314, ADR 0024).** The upgrade note (R-42) and the
> plain `V<n>` scheme are retired: releases are `vX.Y.Z` tags cut by release-please, and
> nothing is announced to a room. The build-time stamp and the three-state read below still
> hold. Read the rest of this file as history.

## Current release tools (issue #217, 2026-10-23)

`version_list()` replaces the old `version(which)` interface. It reads the
existing immutable `VersionManifest` and returns a separate `running` identity
plus recorded version metadata (`version`, `released`, `commit`) in the
generator's newest-first order, without `notes` or `changes` bodies. A readable
stamp with no history still identifies the running build and returns an empty
history list as `ok`; it does not invent a history entry for the running build.

`version_read(version)` reads the selected entry's recorded `notes` and `changes`
from that same snapshot, retaining the reader's case-insensitive matching and
optional `v` prefix. Empty content stays empty. A readable manifest without that
entry is `nothing_found`, including when the running build has no history entry.
This means not recorded here, not that the release does not exist. An absent,
unreadable or unstamped manifest is `unavailable` for both tools. Every outcome
carries a reporting note and records its tool metric.

This is **shipped history**. Releases after the build are invisible until another
image is deployed. The existing generator caps notes at 2,000 characters and
changes at 20 commit subjects: the result is a recorded release summary, not a
complete exact live GitHub release body. Running identity and history remain
fixed for the process. Only the running stamp establishes this process's version;
history alone establishes neither deployment nor that an upgrade caused faster
responses.

The two calls keep budgets 1 and 2, respectively, and remain read-only and
unit-safe. There is no runtime HTTP, GitHub credential, second store, cache or
polling. Generator limits, build stamping and publication controls are unchanged.
Routing cases cover direct version questions, one historical read, and an indirect
upgrade question with sibling exclusions. Generate `nora/version.json` using
`scripts/gen_version_manifest.py` in the clean evaluation checkout before the
live measurement; the selected historical release is `v6.6.9`. Parent-owned live
routing/reporting measurement remains separate from deterministic file-reader
and wiring tests. The old tool and announcement sections below are historical.

# Version, and the upgrade note

## 0. Two questions and one event

Somebody asks 「你而家咩version」. Somebody asks 「V2 有咩」. And, once, after a
deploy, the group should hear that something changed — in her words, not as a
pasted changelog.

Those are one capability with three requirements, and each has a way of going
quietly wrong:

1. **What she reports must be what she is running.** Not what the repository's
   default branch says, not what a release API says — what this container is
   executing.
2. **What she says about an earlier version must be recorded, not inferred.** A
   version whose contents nobody wrote down has no contents, and «I don't have
   notes for V1» is an answer.
3. **The upgrade note must arrive once.** A container that crash-loops every
   four seconds must not post it every four seconds.

## 1. The stamp is baked, because a lookup would answer a different question

The container holds no git checkout, `.dockerignore` excludes `.git/`, and
github.com may be unreachable from it. A runtime GitHub API call would therefore
describe **the repository**, and the repository and the running artifact
disagree for the whole window between a merge and a deploy — which is precisely
the window in which somebody asks what she is running.

So the version is generated on the host at build time
(`scripts/gen_version_manifest.py`) into `nora/version.json`, and copied into the
image by the same `COPY nora/` that copies the code. One filesystem snapshot,
one version.

Two refusals in the generator, both because a wrong stamp is worse than no
stamp: a version that is not `V<n>` (the scheme is plain V1/V2/V3 — no semver,
operator's decision), and a dirty working tree, which would record a commit for
an artifact that never existed.

And one refusal in the image (`scripts/check_version_stamp.py`, run from the
Dockerfile after the copy): the build FAILS when `nora/version.json` is missing,
or when its `version` disagrees with the `--build-arg NORA_VERSION` the operator
passed. That gate is what turns "the reported version cannot disagree with the
running code" from an intention into a property. `scripts/build-image.sh` is the
one command that does the whole sequence, and it carries `--network=host`
(operational constraint 1) so a build cannot fail in apt-get for an unrelated
reason.

### What the manifest holds

```json
{"version": "V3", "commit": "…", "built_at": "…",
 "versions": [{"version": "V3", "released": "2026-08-11", "commit": "…",
               "notes": "<the tag annotation — what the operator wrote>",
               "changes": ["<commit subject>", "…"]}]}
```

`notes` is the tag annotation (`git tag -m …`, which is also what a GitHub
release body carries) and is the **only** thing the announcement relays.
`changes` is commit subjects, capped at twenty, and exists to answer «what was
in V2» — it is deliberately **not** announcement copy. An auto-generated subject
list is not a note anybody wrote, and a Nora who announces
「refactor(agent): tidy the registry」 to a group chat is worse than one who says
nothing.

The operator's own framing was «no need that accurate» — the manifest is a
convenience derived from tags, not an audit log, and nothing depends on its
completeness.

## 2. The read is three-state, and the third state is the whole point

`nora.infra.version.VersionManifest` answers R-27's three states:

| state | means | when |
|---|---|---|
| `ok` | the manifest was read and holds this version | the ordinary case in a built image |
| `nothing_found` | the manifest was read and has **no** such version | «there is no V9» |
| `unavailable` | absent, unreadable, or naming no running version | an unstamped build, a half-written file |

**A missing manifest is `unavailable` and never `nothing_found`.** Collapsing
those two is the exact trap R-27 was registered for, and here it has a
particularly bad shape: a build that forgot to stamp would have Nora state, with
confidence, that she has no versions at all — a false claim *about herself*,
manufactured out of a wiring gap. A source checkout has no `version.json` at
all, so `unavailable` is the ordinary state outside a built image; the tests
point the real reader at real files, because a double for a file-reader would
just be a file.

The read happens **once per process**, and that is grounding rather than
performance: the running artifact cannot change under a live process, so neither
may her answer.

## 3. The tool

One verb, `version(which="")`, following DEC-72's argument against splitting a
capability into tools that then fight over every question: 「你而家咩version」
and 「V2 有咩」 are the same capability from two angles. Budget 2 (one call, plus
the follow-up about an earlier version), `source_tier="confirmed"` — this is her
own build stamp, the closest source there is for a fact about herself, and the
sourcing sentence in the generated capability block is what keeps her from
web-searching her own version.

Its user-facing description lives in the docstring (the schema), and its `when`
clause in `NATIVE_TOOLS`. Nothing about it is written into doctrine: an
`identity`/`conduct` file may not name a tool, the loader refuses to start on
one, and capability text is generated from the registry (DEC-29).

## 4. The announcement, and why it is not a second outbound path

R-1b pins `deliver.build` to exactly two callers. Every guard, the audit-first
write, the Honcho feed and the Langfuse trace hang off that one construction
site, so a module that posted its own announcement would have to re-prove all of
them — and `tests/architecture/test_only_two_paths_can_send.py` makes that a
test failure rather than a review comment.

So the announcement goes **through** the fire path. What differs between "a
reminder came due" and "you were upgraded" is factored into
`nora.app.fire.Occasion`: where she speaks, on whose authority, the words put to
her, and the header that says why this turn is happening. `FirePath.speak`
takes one; `FirePath.run` is the heartbeat's reminder-shaped adapter onto it.

Two things the announcement occasion does differently:

* **`person_id` is `None`.** Nobody asked. §12's «this requester is no longer in
  identity.toml» refusal is about a grant this occasion never held, so it does
  not apply — and the cost is accepted rather than escaped: `ctx.tagger` is
  `None`, so the room-scoped tools and the peer card go dark for that turn,
  which is correct for a turn that is about *her*.
* **The header says relay, not read out.** The note is markdown written for a
  changelog. The header tells her to say what is new in her own words, short,
  and to drop the parts too technical to be worth saying in a group chat.

**Where** she announces comes from `policy.toml` — the rooms the operator
already allowed her into — using each conversation's `primary_id`, never an
arbitrary member of `ids` (`also_ids` holds id forms a platform *retired*, and
sending to one of those fails). It is deliberately not a second list in
`config.toml` that could silently disagree with the first (DEC-8). `[version]
announce` is only the deployment's on/off.

## 5. Once, ever (R-42)

`version_announcements(version, platform, conversation_id)` is the ledger, and
the **primary key is the mechanism**: the claim is
`INSERT … ON CONFLICT DO NOTHING` and answers from `rowcount`, so taking a
version for a room is a compare-and-set in one statement — the same shape
`reminders.claim` uses (R-37). A read-then-write leaves a window, and the window
is not theoretical: a crash-looping container restarts every few seconds, so two
boots can overlap on a slow database and the second is exactly the one that must
stay quiet.

**The claim is taken BEFORE the turn**, and a row existing bars a second attempt
whatever state it holds. That makes at-most-once hold across a crash *mid-turn*
— the case a state check gets wrong — and it is R-37b's decision applied to a
second kind of unprompted speech, for the same reason: nothing links a claim to
a message that may already have reached the group, so a retry double-posts
anything that died after egress. An upgrade note nobody hears costs a `git tag`
and a redeploy; one posted twice cannot be taken back.

Two states deliberately do **not** claim, so they are recoverable:

* an **unreadable stamp** — nothing is announced and nothing is taken, so fixing
  the build still announces the version;
* a **version with no note** — likewise, so annotating the tag and rebuilding
  still works.

And one defers: a **tag holding the conversation slot**. An announcement neither
queues behind a tag nor sheds it (the tick's `try_acquire` rule); nothing is
claimed, and the next boot tries again, which is right because nothing was said.

`nora_version_announcements_total{status}` is the trace of every one of these.
R-5 keeps failures out of the room, so on a bad day the counter is the only
evidence — and `already_announced` climbing while `sent` stays put is what R-42
holding actually looks like on a dashboard.

## 6. What is deliberately not here

* **No re-announcement of a failed note.** See above; at-most-once is the
  decision, and the row records why.
* **No `nora versions` CLI subcommand.** The control plane is read-only and the
  question it would answer (`what has been announced`) is one `SELECT`; add it
  the day an operator actually wants it.
* **No semver, no `V3.1`.** The scheme is plain V1/V2/V3 by operator decision,
  and the generator refuses anything else rather than silently accepting a
  format the rest of the system cannot match on.
* **No per-version doctrine.** She is told what changed; she is not told to
  behave differently because of it.
