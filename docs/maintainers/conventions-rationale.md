# Conventions & operational detail

The long-form behind `AGENTS.md`'s rules. `AGENTS.md` carries the terse rules an
agent must not violate; this file carries the *reasoning*, the war-stories, and
the specific values. Read it when a rule needs its "why" — every mistake recorded
here cost an afternoon, and the terse rule is only safe because the story is
preserved. Nothing here is optional context; it is the load-bearing detail moved
out of `AGENTS.md` so that file can stay scannable.

## Gate commands — the detail

The schema gate is `tests/infra/test_schema_parity.py`, not the `alembic check`
CLI. `alembic check` guards column presence, column type and server defaults;
it is provably blind to the CHECK constraint and to the `ts DESC` index
ordering, and `idx_messages_conv_ts` is excluded from autogenerate because the
expression index otherwise reports drift on every run. Both run in
`uv run pytest -q`. See DEC-11 in the decision index for the full story,
including a 2026-08-05 correction to its original server-default claim.

**Dropping the CLI once meant dropping the half of the job it was good at**
(2026-08-16). The two tools were complementary and only one survived: the
catalogue reads above cover the CHECK and the DESC ordering, but for a while
nothing compared `schema.py`'s `MetaData` against the migrated database at
column level. Every other assertion in that file compares the database against
something else derived from the migrations — the existence check never looks at
a column, and the dump/baseline test compares a `pg_dump` against a file
regenerated from that same dump — so `schema.py` was in no loop at all, and a
metadata type that disagreed with its migration was invisible. That matters
because `METADATA` is what the ORM issues queries through: a type it is wrong
about is a query built on a false premise. `test_the_metadata_matches_the_
migrated_database` restores that half in-process, via the same autogenerate
comparison the CLI uses, filtered through the same DESC-index exclusions so the
phantom drift that made the CLI unusable here does not come back with it.

`uv run lint-imports` uses a local cache (`.import_linter_cache/`, gitignored).
If it reports a phantom contract failure that doesn't reproduce after a clean
checkout, delete that directory before spending time on it.

There is no longer a module-size gate. Spec §10's 300-line hard cap was
deleted 2026-08-06 — see the Conventions section below for why, and prefer
counting a module's responsibilities to counting its lines.

**What "types clean" actually covers.** The `typecheck` target in the
`Makefile` is the list: 13 packages, and the strict override in
`pyproject.toml` (`[[tool.mypy.overrides]]`) names the same 13 (#121,
re-measured 2026-09-17). Everything else under `nora/` (`agent`, `compute`,
`platform`, `testing`, `obs` among them) is not type-checked, so a green gate
means "those 13 are", not "this repo is". Measure a package's OWN errors by
scoping the override to it, never by putting strict flags on the command line:
those leak into every imported module and once inflated a count 15x
(`pyproject.toml` carries the method).

The gate is the FINAL check, not the iteration check. It takes ~8 minutes; running
it after every edit instead of `make test-affected` costs an hour a day and buys
nothing — AGENTS.md previously named only the serial form, and that is exactly the
mistake it produced (2026-08-21: eight full gate runs in one session).

## The "Never" rules — with the war-stories

- **Never `pip install`.** Dependencies are added with `uv add`, never raw pip
  — this is one of the five operational constraints below.
- **Never write a test whose fake answers every request the same way.** Three
  separate no-ops shipped green this way and each was invisible for months:
  signal-cli ran without `--receive-mode` so no websocket ever opened; the SSE
  parser required a `method` key the HTTP transport does not send, so every
  real message was dropped; and `HonchoClient.ensure_session` POSTed to a
  **list** endpoint, so `feed()` returned `False` before making a single HTTP
  call and Nora's semantic memory received nothing for the entire life of the
  deployment. That last one had a passing test — it asserted the wrong URL,
  against a fake server that returned `{"id": ...}` for *any* path, so it
  asserted the implementation back to itself. **Make the fake behave like the
  real server**, including the shapes it returns for the wrong call. If
  reverting the code under test does not turn the test red, the test is
  decorative.
- **Never paste production rows into a fixture.** A production-data fixture
  once passed review because preserving test coverage was mistaken for permission
  to publish the source data. Use a synthetic generator instead.

  **Deleting a file is not deleting an object.** A committed blob remains in
  history, clones and archives after its path is removed. Review what enters Git
  before committing; a clean current tree does not establish safe history.

  Three lessons, in the order they bite:

  - **The privacy question is not the testing question**, and being right about
    the second does not answer the first. "No narrower slice is safe" is a
    reason to GENERATE the corpus, not to commit the real one.
  - **`git rm` is not a deletion.** Anything that reaches a commit reaches
    every clone, every fork and every archive of it, permanently. The moment to
    refuse is `git add`.
  - **This repo gated behaviour and gated the tree not at all.** R-18 scans
    every byte on its way out to a room; nothing scanned a byte on its way into
    a commit, and they are the same disclosure with a different audience.
    The identifier gate that closed it (removed 2026-09-16: it re-scanned all
    of history on every test run — 5.77 GB RSS, OOM — where the job belongs
    pre-commit, on staged bytes only) did not at first, and the gap is worth
    keeping written down because it is easy to make again. It read `git
    ls-files`, i.e. the INDEX, while the incident above is about a blob that
    survived `git rm`. Replaying that incident exactly — add corpus, commit,
    `git rm`, commit — left it green with production data reachable
    from `HEAD~1`. It also failed OPEN four ways: one appended non-UTF-8 byte,
    a `.svg` rename, a symlink, or a UTF-16 encoding each took a payload past
    it silently, while its own docstring named that exact shape as the one that
    must never happen (#166).

    What it did by the time it was removed (it no longer runs; #283's
    pre-commit detector replaced it, below): walked every git OBJECT,
    every commit MESSAGE, and the working tree — objects being what would be
    published and the working tree what is about to become one. Undecodable content, and a tracked path that
    is not a regular file, were ERRORS with a declared allowlist rather than
    skips; there was no suffix skip list at all. Values were NORMALISED before
    matching, so a separator-formatted number and a dashless UUID collapse to
    their declared form. The coordinate rule was shape-agnostic — comma-, tab-
    and JSON-separated rows all count — and a location table must be DECLARED
    WITH A SIZE, never merely by path: exempting a path outright let the next
    corpus be pasted into a file already on the list.

    Its replacement (#283, 2026-09-26) is `scripts/pii_scan.py`, run by the
    `pre-commit`, `commit-msg` and `pre-push` hooks (`.githooks/README.md`).
    It keeps the object-level look — pre-push reads every blob, message and
    author identity a push introduces, so the `git rm` replay above fails
    there — and judges only what a change ADDS, which is what makes it
    seconds, not a history scan. The real values sit in a denylist outside
    the repo, matched after normalisation. The four structural holes the old
    gate left are the new one's design: coordinates are read as FIELDS and
    scored on dwell (rows x places), not literal count; a declared table is
    pinned to its CONTENT digest, not its size; declarations and placeholders
    are read from the newest ancestor the remote already has, so a
    declaration cannot land beside, be amended into, or be pushed with its
    payload; phones written with a `+` are detected in every country, and the
    denylist is matched through separators, look-alike letters, Traditional
    and Simplified Chinese, and decoded JSON, URL, HTML and base64 layers. UTF-16, undecodable
    bytes, symlinks and gitlinks stay ERRORs with a declared allowlist, and a
    declared binary is scanned for embedded strings, never skipped. Every
    rule is guarded by a named test, shown by a mutation table
    (`scripts/mutations/pii-precommit-detector.json`). Since round 3 it is
    deterministic end to end -- the model adjudicator and commit judge were
    removed, and a finding clears only by a pushed declaration, the private
    pseudonym registry or a human acknowledgement. It is still not a
    control against a motivated author, and not what decides a public
    release (#167). A human acknowledgement requires LOOKING at the value.
    Never acknowledge a value yourself: replace it with a generated one, or
    ask your human.

    Falsified by attack, not by assertion: an undeclared identifier planted in
    the gate's OWN source, a table pasted into a declared path, a table at an
    undeclared path, and a payload renamed `.svg` — all four caught, all four
    having passed the version before. Its name half is only a
    regression pin — there was no allowlist over English — and the file said so,
    because a green gate must not be read as «no real names in the tree».

- **Never point signal-cli at a different account, re-register it, or unlink
  its device** without an explicit decision — that store holds the device
  registration, and losing it means re-linking. Nora *does* now own the daemon
  (see constraint 3); routine reads and RPC calls are ordinary, destructive
  account operations are not.
- **Never reuse an archived legacy gateway profile path** — the legacy gateway
  is retired; see constraint 4.
- **Never assert a per-tag object was cleaned up by checking that an
  attribute was deleted.** `Handler._prefills`/`_guards` (the leak the
  pipelines phase exists to remove) existed precisely because tests asserted
  on retained *constructed* objects, not counted live ones — an
  attribute-absence check would have passed anyway.
  `tests/architecture/test_no_retained_per_tag_state.py` asserts on
  `gc`-tracked live `ReplyGuard` counts across two different batch sizes
  instead, so the same leak shape can't reappear under a new attribute name
  and still pass.
- **Never read an instrument's output as evidence without first checking it
  can measure the thing.** Absence is not evidence when the instrument was
  never wired to produce presence. On 2026-08-06 a grep of `data/nora.log` for
  `lookup_person` returned nothing and was read as "the tool is never called" —
  but no tool logged its own name on any success path, so that grep returned
  nothing whether it ran every turn or never. Two confident architectural
  conclusions followed, one of which nearly retracted a design document, and
  the correct answer turned out to be the opposite (6/6). Before concluding
  from a null result, state what a POSITIVE result would have looked like and
  confirm the system can produce it. This is the same discipline as the
  fake-that-answers-everything rule above, applied to observability instead of
  tests.
- **Never claim a merge, deploy or fix without reading back the state you
  claim.** Also 2026-08-06: `git merge` reported "Already up to date" because
  the session was *on* the branch it was merging; that was reported as merged,
  and master stayed fourteen commits behind while the container ran branch
  code. `git log --oneline -1`, `docker exec … python -c "import config"`, the
  metric endpoint — one command, every time. The check is cheaper than the
  correction.
- **Never edit, commit, or merge on `master` in this checkout.** 2026-08-20: a
  session ran five worktree agents correctly, then merged all five, committed two
  specs, and pushed — all directly on `master`, in the checkout the running
  container bind-mounts. `nora/doctrine` is mounted read-only from this working
  tree and `[doctrine] reload = "mtime"`, so the doctrine changes went live on
  the *next message* with no build, no deploy step, and no gate between the merge
  and the group reading it. Nothing broke, and nobody decided to deploy — the
  merge deployed itself. The same session also swept two untracked spec files and
  an unreviewed `AGENTS.md` into a merge commit via a reflexive `git add -A`
  while resolving a conflict.

  Both failures share one cause: on `master` there is no step between "I changed
  a file" and "it is live and public". A worktree restores that step. Every
  change starts on a branch — a one-line doc fix included — and reaches `master`
  by merge, so the merge is the moment somebody decides, rather than a side
  effect of editing.

  **Checkout-era history:** the following source-mount account predates the
  installed Komodo release path; doctrine and Dreamer are now image-baked.
  The host checkout remains a live config/infrastructure runtime root; do not
  delete or reset it.

  **The bind-mount source is `nora-host`'s own
  checkout (`~/workspace/nora`), not this one** — read back 2026-09-08: the
  compose bind sources are relative to the compose file on the host, so a merge
  here reaches the container only when `nora-host` pulls. An older topology, in
  which the dev checkout WAS the bind-mount source, is what the previous
  wording described; it is superseded, and it mattered in both directions — it
  made one session sequence a deploy around a window that did not exist, and
  read while standing on `nora-host` it points at the wrong machine. In checkout
  mode an edit to `nora/doctrine` on `nora-host` was a deploy (it hot-reloaded on
  mtime). Under the installed release path, doctrine changes reach production
  only through a release image. `config.toml` there is still live once its
  services are recreated.
  **`nora/skills` is NOT** — it is `COPY`d into the image (`Dockerfile`) and the
  compose file binds only `nora/doctrine` and `nora/dreamer`, so a skill edit
  needs a rebuild. Editing one on the host the way you would edit doctrine
  deploys nothing and reports nothing.
  **Landing a merge does not move you back
  in** — the next change starts in a new worktree too. 2026-08-21: a session
  merged, kept editing in place, and another session was seconds from building
  an image out of a mid-refactor tree with 16 tests red in it.

- **Never run two agents against this checkout at once.** Three times on
  2026-08-06 a subagent's `git add -A` swept another session's staged work into
  its commit, and once an agent amended a commit it did not author. Nothing was
  lost, but commit history stopped describing who changed what. Sequence them,
  or give each a worktree.
- **Never run `alembic revision --autogenerate` and trust the result for
  `idx_messages_conv_ts`.** It is excluded from autogenerate on purpose; a
  revision that touches it must be hand-written.
- **Never build a model prompt outside context assembly.** Operator, 2026-10-01:
  every agent assembles through context assembly, "or it goes back to the
  original chaos, everything built differently". The census that was meant to
  hold the line read a hand list of five files, so the Cognition session, built
  after the list was written, hand-wrote its whole prompt (no blocks, no
  escaping, an id shape its own `note_create` refused) and nothing noticed. The
  enforcer now finds occasions by AST and a violation is a plain failure: there
  is no debt ledger to hide one in, and no exception for an occasion. The only
  modules that are not occasions are the named MECHANISM, RUNNER and EXEMPT
  entries in the test, each with a written reason, reviewed in the PR that adds
  it and re-checked by the test. A new occasion follows
  `design/context-assembly/04-every-occasion.md` «How a new occasion joins»
  (DEC-273, ADR 0021).

- **Never read a gate's success through a pipe.** `make gate | tail` returns
  `tail`'s exit code, and a backgrounded `cmd; echo done` reports `echo`'s:
  write the log, capture `RC=$?`, then read the log. Four incidents before it
  was written down; one merged a red `master`.
- **Never cite a file, test or function you have not opened** in this session.
  A peer saying it will write one is not an opening, and finding a definition
  is not finding a caller: grep for the call before repeating that it works.
- **Never assume your migration's parent is unclaimed** — two branches cut from
  the same head each write `down_revision = <that head>`, git merges both files
  without a conflict, every test that does not actually migrate stays green, and
  the container fails at boot because `upgrade "head"` refuses two heads.
  **`uv run alembic heads` must print exactly ONE line after every merge.**
- **Never take a DEC or R number from a chat agreement** — re-read
  `design/09-decision-index.md` at MERGE time, not at claim time. 2026-08-21:
  two sessions coordinated numbers carefully and a third took DEC-131 anyway;
  it conflicted only because the rows landed on the same line. Allocation and
  duplicate checks now live in `nora-decision`, including origin/master and
  open PRs. The old check needed a trailing `\|` so sub-lettered rules
  (`R-6b`, `R-10c`) were not read as their
  parent number, and the old check reported 8 duplicates that were not. 2026-08-23: a
  REAL duplicate — two rows numbered R-29 — sat on master for 15 days, reported
  and backlogged by the session that found it, because the check that should
  have caught it cried wolf seven times. A noisy gate is a disabled gate.

## Reaching the deployment — the detail

The terse rule is in `AGENTS.md` («Reaching the deployment»); this is the why.

**Checkout-era history:** the following explains the earlier checkout deployment
model. The installed Komodo release stack now owns application deployment;
`~/workspace/nora` remains the live config/infrastructure runtime root.

**Two machines, one direction.** Until 2026-09-07 sessions ran on the host
itself, committing directly in the checkout the container bind-mounts.
That is the root of every «the merge deployed itself» story above: the build
context, the doctrine mount and the editing surface were one directory. The
fix is structural rather than procedural — the host holds a checkout that only
a deploy moves, and development sessions run on a separate machine. Host reads
use `ssh nora-host`, the operator-configured SSH alias documented below;
changes use the locked deployment procedure.

Configure `~/.ssh/config` with your own address, login and dedicated key:

```sshconfig
Host nora-host
    HostName <deployment-hostname-or-address>
    User <deploy-user>
    IdentityFile ~/.ssh/nora-host
    IdentitiesOnly yes
```

- **Key auth only.** `~/.ssh/nora-host` is a dedicated ed25519 key, installed once
  with `ssh-copy-id`; `Host nora-host` in `~/.ssh/config` binds user, host and key.
  **Never `sshpass`, never a password in `.env`, in chat, or in a file a session
  reads** — a password typed into a session is transcribed into memory (this
  happened once), and `.env` is the compose environment on both machines.

**Why a dedicated key and nothing else.** On 2026-09-07 the first attempt to
reach the host from a session found four local keys, none authorised, and a
host that also accepted passwords. `sshpass` was installed and the operator
offered the password. It was declined: a password that passes through a
session is written into the transcript and into Honcho's memory of the
operator — and a search of that memory showed a host password from an earlier
session already sitting there. `ssh-keygen` + `ssh-copy-id` costs one typed
password that never touches the session; after that there is nothing to leak.
Nothing in `.env` either: that file is the compose environment, present on
both machines, and a shell password there is one `cat .env` from any container
debugging session.

**What a read-back actually looks like.** The 2026-09-07 note-tier read-back
is the template: the checked-out commit, `docker compose ps`, the live
`[notes]` block of `config.toml` (which showed `candidates_enabled` absent — a key deleted 2026-10-03, DEC-277 —
the flag the worktree's stale copy could not have told you), the row counts,
`dream_schedule_state`, and the `nora_dream_*` counters. That single pass
settled in five minutes a question three design nodes had been arguing from
the code alone: the note create path had never run, and the two
`note_candidate` rows were `kind = 'miss'` — `note_search`'s negative cache,
not proposals.

**Two traps found on the way.** `docker ps | grep db` matched an unrelated
database container; `docker compose exec db` from the project directory is the
only address that cannot be wrong. And `docker compose logs nora` was empty —
the container did not use the docker log driver — so «no dream lines in the
log» was not evidence of anything. The metrics endpoint and the tables were.

**This is the trap that eventually got fixed rather than documented again.**
The log was a file because `nora/obs/logging.py` landed on 2026-08-03 and the
Dockerfile on 2026-08-04: a host process's sink, never revisited when the
process moved into a container, and never covered by a decision row. The
response at the time was to write «container logs are not on the docker log
driver» into three files. It was still costing readers a year-of-the-repo
later — a session in September read it, then reached for `docker logs nora`
anyway. On 2026-09-09 the handler moved to stdout and the `path` setting was
removed. The lesson is not about logging: **a surprise documented in three
places is a surprise that should have been deleted.**

## Configuration — history and validation

**All three are gitignored now** — `config.toml` joined the other two on
2026-09-07 (operator decision). It was tracked on the argument that it holds no
secret values, and what made that untenable was collision rather than secrecy:
the live file is a bind-mount source the operator edits in place, so every
merge touching it fought an uncommitted working change and each session had to
stash-merge-restore-diff by hand to keep from losing an edit. `config.example.
toml` is now the only committed record of the config's shape, and
`tests/test_env_template.py`'s three drift checks compare the two — but
`config.toml` is gitignored in every dev worktree and in CI, so they SKIP
there by design (`tests/test_env_template.py::_load_live_or_skip`); nothing
in the established workflow (worktrees only, never the deploy checkout —
"Reaching the deployment" above) runs them for real. What
DOES run automatically, once per eval, is a different and narrower property:
`evals/subject_config.materialise()` rebuilds the eval subject's config from
the live `config.toml` and REFUSES to start the run if the two still disagree
outside `ALLOWED_TO_DIFFER` (issue #321). It does not subsume the drift
checks — it self-heals around a template that is stale or incomplete rather
than requiring the checked-in text to be right, so it protects the *computed
eval config*, not `config.example.toml` itself as the thing a human reads to
reason about the deployment. That is still only `test_env_template.py`'s job,
and it stays SKIP-based (a fixture would compare the template to itself,
green forever, measuring nothing — the exact anti-pattern the file's
docstring warns against).

**No config change reaches a process unvalidated** (deploy convention §4.5,
implemented 2026-09-08): `nora config set/get/unset/validate/path --config
<path>` validates against the schema, prints what changed, and never touches
`.env` — secrets stay there and are referenced by name. On the deploy host the
CLI cannot write the live file from inside the container (`EBUSY` on the file
bind-mount, 2026-09-09), so the checkout-era procedure is legacy `nora-deploy` reference step 5: edit a
COPY, `nora config validate` the copy through the running container, `cp` it
into place (in place, so the mount keeps its inode), recreate, and restore the
backup on any failure. An unvalidated hand edit of the live file is never the
procedure: `extra = "forbid"` catches a broken file only by crash-looping.

`config.toml` is ALSO bind-mounted read-only into the two voice sidecars
(`stt`, `voice-wake`): they read `[speech.stt]` / `[speech.wake]` with stdlib
tomllib at boot (2026-09-17 — before that, the same knobs were env vars in
compose and disagreed with this file). Identity and policy are bind-mounted read-only and **hot-reloaded after
validation every three seconds**. Invalid edits retain the last good version;
workspace-binding edits refuse the whole reload until recreation. The
`nora_config_reloads_total` outcomes and `/health` staleness expose pending or
refused changes (see `design/policy-reload/00-design.md`). `identity.toml` takes one
`[[persons.<id>.<platform>]]` block **per account**, all resolving to one
`person_id` (their Honcho peer, A2/DEC-14). `policy.toml` is deny-by-default and
Nora **refuses to start** if it is missing or allows nothing (R-5). Memory's
hierarchy is split across them on the same lines (DEC-214): `policy.toml`
`[units]` says which workspace each unit is in, `identity.toml` `[workspaces]`
binds each workspace to its Honcho workspace, and `honcho.workspace_id` is only
the binding used when there is no `[units]`.

## Design discipline — incidents

2026-10-03: two days built delete-withdrawal cascades, lineage, a shadow
compare and a live ledger around a loop that had never written a row. Live
there had been 2 deletes ever, and Honcho keeps the deleted text anyway: it
has no per-message delete. `[lineage]` was built, marked do-not-enable, and
leaky anyway because Memory has no lineage. The unit is the read boundary.

2026-08-27: one session flipped three times on the same build-vs-vendor question
with **no new measurement between the flips** — which means it never had a
decision, only a preference.

Asking for a decision available from evidence is ping-pong: the operator
restated this at the start of work more than a
dozen times between 2026-09-08 and 09-22.

The design-backlog example is issue #4: a per-conversation rolling summary,
updated in place and separate from the Dreamer and Honcho.

Tool policy provenance (DEC-200): ADR 0007's rules had no test and were not
met even on the day they were written; ADR 0015 §6 names each enforcer.

## Historical reading references

**A `docs/superpowers/…` path is a historical citation, not a location.** That
tree — 34 dated specs and 35 plans — was **removed 2026-09-08** along with
`.superpowers/`; the process it named is gone and is not coming back. The paths
still appear in design nodes and docstrings as provenance for
where a decision was first written, and they resolve only in `git log`. Treated
the same way as a `DEPRECATED` decision row: marked, never rewritten to point
somewhere it does not point.

**Deprecated 2026-09-07 — the knowledge tier (the "fact tier") and the language
tier.** Both are gone: the code is deleted, the decision-index rows are marked
(never renumbered), and every design node and spec that describes them carries a
`DEPRECATED` banner. **Their database tables are NOT dropped** — the code goes,
the schema stays, and dropping them is a separate explicit decision. The evidence
that decided it is `design/dreamer-agent-loop/20-knowledge-tier.md` §0; the issue
is #115. Nothing under `design/dreamer-agent-loop/` numbered 20, 21, 23, 24, 25,
28, 31 or 32 describes code that exists.

**Deprecated 2026-09-08 — the note tier's entity-resolution chain and the
curator's merge half.** With nothing minting entities, `note_entity` could never
be populated and merge was inert by construction; the registry store, the
resolution pass and the merge verb are deleted (their tables stay). The
curator only retires. R-75 in the decision index says which clauses survive. A
model-judged merge returns in a separate `note-merge` adaptor (DEC-202, design
41 §7, designed 2026-09-24, built 2026-09-25 behind a `merge_mode` key that has since been
removed, first run in `shadow`, which wrote no note); the curator
stayed retire-only.
**2026-10-03 (DEC-277): `note_candidates`, the curator and `note-merge` are
deleted; the Cognition session writes, retires (`note_retire`) and merges
(`note_merge`) notes, and `[notes] enabled` is the only notes key left. The
paragraph above is the history.**
The create gate in `note_candidates` matched on anchor date alone from that
day until **2026-09-13**, when two firings in production merged two unrelated
occasions twice (#228); it now resolves the note title the MODEL names, and
the date is only when the thing happens.

## The instruction is the first thing blamed and usually the wrong one

*(The rule is `AGENTS.md`'s Measure verb: when the measurer is a MODEL, its
input has to explain itself. This is where it came from.)*

On 2026-09-09 the Relevance Gate answered `no_go` with confidence 0.0 to a
plain question about tomorrow's weather, which its own instruction names as
something she can look up. The instruction was rewritten three times over the
next hours, each version judged against that one live window:

| | false NO_GO / 40 | safety | dead capabilities |
|---|---|---|---|
| the shipped instruction | 3 | 6/6 | — |
| + its derived tool list | **5** | 6/6 | — |
| principle only, shapes removed | **15** | **3/6 BREACH** | whereabouts, settled |
| a CMS-structured rebuild | **16** | 6/6 | whereabouts, settled |
| + sharper record-class prose | **16** | 6/6 | whereabouts, settled |

Three things worth keeping from that table. **Telling the model what it CAN DO
made it stricter, not better** — the derived tool surface was the obvious fix
and it lost cases in both capabilities it existed to expose. **Removing the
enumerated shapes breached a safety bar**: without one sentence saying a
transcript line is a member's claim about her and not her word, the gate
believed a forged commitment. And **naming the record classes in careful prose
moved nothing at all** — `whereabouts` stayed 0/9 across two wordings. This
model does not learn what she holds from description.

What fixed it was not the instruction. The gate was handed two tagged blocks
and no statement of what either one was, while the tag path has always given
Nora a `<context_header>` naming every block in front of her. It was being
asked whether a room had moved past a thread, was still waiting, or had already
been answered — **three questions about time — from a transcript with no
timestamps and no statement of which end was newest**, and from a `<standing>`
block that arrived unexplained even though the instruction calls it the only
thing that can establish what she owes a room. Rendering an age per row, saying
which end is new, and explaining the block took false NO_GO from a median of 3
to 1 over six runs, one of them scoring zero with every capability full.

`nora/agent/prefill.py`'s `_age` had recorded the identical failure one path
over, in 2026-08: without an age «she could not tell a five-minute-old line
from a five-day-old one, which is what made 今晚 and 琴日 unanswerable even with
a clock in the header». The fix was applied to the tag path's transcript and to
nothing else. A CMS audit that night found the same omission in two more
places: `<standing>` asserted it was current while carrying no time, and the
note curator (deleted 2026-10-03, DEC-277) was asked whether an anchor had passed and given no clock at all.

**The cost of the wrong instinct was five hours and a shipped regression.** The
instrument that settled it — 40 labelled windows, one command — already existed
and was not reached for until the third rewrite.

## Instruments and spans — the war-stories

*(How the pieces fit together — the signal boundary, the path a span takes to a
collector, how to instrument a model call, how to read it back — is
`docs/observability.md`. This section is why the rules are what they are.)*

`design/dreamer-agent-loop/02-instrument-blindness.md` is the catalogue: eight
layers where an instrument could not observe the thing it existed to observe,
plus the silent-match family one level down — the instruments we use to FIND
those layers returning a positive-looking answer without ever engaging.
`design/dreamer-agent-loop/09-measurement.md` is where the bundle gives that
discipline a home for its own change. Neither is repeated here. This section
carries the eight rules and the measurement behind each.

### One root span per entry point

`nora/ingest/pipeline.py` is the pattern. `run()` opens `span("ingest")` around
`await self._run(...)`, and the stage spans — `ingest.scope`, `ingest.identity`,
`ingest.media`, `ingest.audit`, `ingest.memory_feed`,
`ingest.gate` — nest inside it (cited by name: the line numbers this paragraph
once carried went stale within a week). Splitting the body out of `run` is
what makes the root wrap EVERY exit including the early returns, which is why
`_outcome()` can stamp `nora.outcome` on a refusal instead of leaving a trace
that simply stops.

`nora/engagement/pipeline.py` did the opposite until 2026-08-28:
`engagement.admission`, `engagement.debrief` and `engagement.invoke` were three
siblings with nothing above them, so one tagged message produced three separate
traces plus the one from ingest. The operator's first reading of the new
skeleton was «10+ separate trace but its empty», and both halves of that
sentence are a different rule below. The root landed as
`span("engagement", TRACE_KIND="generation", …)` in `EngagementPipeline.run`,
with `engagement.turn` the second declared root for the queued-tag half.

A rootless span is not a span with a missing parent. It IS a trace.

### Langfuse gets the traces with a generation in them (R-76)

One root per entry point says wrap everything. It does not say every entry point
belongs in the collector, and for two weeks the codebase read it as if it did.

`nora/ingest/pipeline.py` opens six spans and there is no model call under any
of them — six deterministic stages, a row, a feed. It is also the
highest-volume entry point in the system, so the operator's Langfuse was **97 of
its last 100 traces from ingest** (measured 2026-08-28, across 12 rooms Nora
does not serve). The obvious reading is a volume problem with a sampling answer,
and DEC-142's reversal condition said exactly that. It was the wrong axis: even
the three traces that were not refusals carried no generation, so no *fraction*
of them belongs in an LLM-observability tool. Langfuse holds traces of model
calls; «how many messages ended which way» is a Prometheus question and was
always going to be answered badly by a trace.

**The rule is about ROOTS, and that is the part worth getting right.**
`engagement.admission` has no model call in it either — and it must keep being
exported, because as a CHILD of a trace that has one it is how a turn that ended
before the model ends visibly at a named stage. A per-span rule would leave
every real trace with holes exactly where an early exit needs to be legible. So
the sampler (`nora.obs.tracing.build_sampler`) is composed under `ParentBased`:
the delegate decides roots, children inherit and never re-decide. That is also
what keeps a QUEUED tag's turn — re-attached to a captured context whose root
span has already ended — in the trace that admitted it.

**It is «CAN contain a generation», declared on the entry point.** A sampler is
called at root start and cannot know whether a model call will happen; and a tag
refused at admission never reaches one, yet its trace is precisely what DEC-141
exists to preserve («it SAYS it was rejected» rather than an unexplained stub).

**Fail-open.** An undeclared root, and an unrecognised kind, are exported and
logged. Getting it wrong towards export costs noise, which is visible and
complains about itself; getting it wrong towards silence costs a missing trace
for a model call — an invisible gap in exactly what the rule protects.

**Nothing is lost, only moved.** `nora_ingest_outcomes_total{platform,
conversation_type, outcome}` carries what the root carried, with `IngestOutcome`
as its derived label vocabulary. Everything else was already counted and no
duplicate was added: `person_known` is `nora_unmapped_senders_total` against
`nora_messages_total`, attachments are `nora_media_custody_total`, the two
refusals have their own counters, and `engage_reason`'s one load-bearing value —
`policy_error`, the gate degrading closed under R-5 — is
`nora_errors_total{stage="engage_gate"}`.

**The trace is unexported, not deleted**, and that is deliberate twice over. It
is what makes «the collector gets nothing» a measurement of the RULE rather than
of an absence — a pipeline that opened no spans would satisfy that assertion for
the wrong reason, and the test that reads the dropped trace with the sampler
removed is the positive control. And it makes the reversal one attribute rather
than a re-instrumentation.

**The test provider runs production's sampler.** `tests/conftest.py`'s
`exported_spans` is built with `build_sampler()`. A test provider that sampled
everything would export spans the deployment drops, so every «what reaches the
collector» assertion would be reading a provider no deployment has — the
silent-match failure, inside the instrument that exists to find silent matches.

**This holds unless** a plumbing entry point turns up whose trace an operator
genuinely needs, which is one declaration on that root. The answer is never to
widen it back to «export everything and sample».

### Spawned work does not inherit the caller's span

`asyncio.create_task` **does** copy the current context — the repo already says
so about `tag_id` in `nora/agent/tools.py` (the `bind_tag_id` comment), and a
three-case probe reproduces it. What it copies is the context of the task that CALLS
`create_task`, at the moment it calls it, and that is not always the logical
parent:

| what was spawned | the context it ran under |
|---|---|
| a task created inline, inside the span | the caller's — nesting works |
| a factory built here, INVOKED by another task | **that other task's** |
| a task created before the context was set | none |

The queued-tag path is row two, exactly. `EngagementPipeline.run` stores a
zero-argument factory; `ConversationSlots.release_and_dispatch` calls
`self._spawn(factory())` (`nora/engagement/slots.py`) from the PREVIOUS turn's
task, in its `finally`; and `BackgroundTasks.spawn` (`nora/app/runtime.py`) is
`asyncio.create_task`. So a QUEUED tag's `invoke` and `debrief` run under
the releasing turn's context, never under the dispatch call where its own
`admission` ran — and the two halves of one tag land in two traces even once
the root above exists.

If work is spawned, capture the context you mean and attach it. Inheriting one
by accident is not the same as propagating one on purpose.

### Model spans, detached work, logs and metric declarations

- **One spelling for "this span is a model call" — and it is Strands'
  own, not this repo's, since 2026-09-19.** An Agent-backed call
  (`nora.agent.structured.ask_text`/`ask_structured`) needs no
  instrumentation code at all; a raw `chat.completions.create` call
  instruments through `nora.agent.telemetry.start_raw_model_span`/
  `end_raw_model_span`. A call site never writes a `gen_ai.*` or
  `langfuse.observation.*` literal by hand, and never wraps an
  `ask_text`/`ask_structured` call in a span of its own — the Agent already
  opens one, and a second span around it is two overlapping traces for one
  call (the debrief/standing fold's own incident: `4448c737` moved them onto
  the SDK, nothing removed the hand-rolled wrapper still around the call, and
  every fold produced two nested traces for months).
- **Detaching a span and dropping it are two decisions.** Spawned work should
  detach so it does not hold a trace open; that is not a reason to declare it
  plumbing. Conflating them hid every media read from the collector for two
  weeks while its counter recorded them.
- **A log line is not a metric.** If the answer is "how many, how often, with
  what outcome" it is a counter; a line that restates one every interval is a
  third copy. 78% of the deployment's log was two such sources.
- **Severity is a NUMBER as well as a word** — `level` sorts alphabetically, so
  "at least a warning" is not expressible over it. `severity_number` carries
  OTel's SeverityNumber bands; an unmapped level gets no field, never a guess.
- **And an increment with no declaration is worse** — `Metrics.inc` raises
  `KeyError`, which the caller's own `except Exception` then reports as the
  failure of whatever it wrapped. `nora_peer_card_empty_total` spent a
  deployment turning every genuinely empty card into a fake transport error.
  `tests/architecture/test_every_recorded_metric_is_declared.py` is the gate;
  a call site whose name it cannot read statically must be PINNED there, never
  skipped.

### A span with only a name is worse than no span

As of 2026-08-27 (`d673a7a`): ten spans on master — seven in ingest, three in
engagement — of which exactly ONE, the ingest root, passed attributes of its
own (`nora.platform`, `nora.conversation_id`); `_outcome` added a second on the
early returns. Every other span carried a name and nothing else, and the
operator's report was that the traces were empty. (The roots and their
attributes landed the next day; the count is the war-story, not the tree.)

Prefill sizes and which blocks were built, store-read counts on the critical
path, the model's stop reason, the delivery outcome — none of it is on a span,
and all of it is what the disclosure and budget arguments in this repo reason
about. The span exists; the re-derivation it is credited with does not.

An absence prompts the question. A page of named-but-empty traces answers it
wrongly, is harder to work with than nothing, and reads as progress.

### Instruments measure the result; something must measure the cost

The sharpest instance this repo has recorded.
`SqlTurnVerdictStore.latest_ungrounded` shipped with `.is_(False)`, which
renders `grounded IS false`; `idx_turn_verdict_ungrounded` is declared `WHERE
(grounded = false)`, and Postgres's predicate prover cannot show a
`BooleanTest` implies an `OpExpr`. The partial index was unusable, so the one
read the debrief stage makes on every fold, inside the held lock, sequentially
scanned the whole verdict table.

**Measured at 60k rows: 3.063 ms over 525 buffers against 0.109 ms over 12 on
the index — with an IDENTICAL answer.** Right row, right order, both
implementations agreeing, every test green, two reviews clean. The divergence
lived only in the plan, and nothing in this repo had ever looked at one. The
fix is `== sa.false()` (`nora/infra/sql/turn_verdicts.py:100`) and the check is
`tests/contract/test_turn_verdict_store_contract.py::test_the_trigger_read_
actually_uses_its_partial_index`. (Both were deleted on 2026-09-24 with the
grounding verdict, `design/standing-compact/`; the lesson stands.)

**And the first version of that test compiled its OWN statement** with the
correct spelling and EXPLAINed that — so reverting the store's predicate to the
exact defect left it green, measured. The SQL is now captured off the engine as
the shipped method issues it. A test that spells the predicate pins today's
fix; a test that captures it pins the property.

A claim about cost — an index is used, a call is bounded, a read is scoped — is
never checked by reading the answer, because the answer is what stays correct.

### R-27's three states, pointed at the instrument

R-27 governs what Nora says. It governs a counter identically, and three
instances landed in one night, each inside code written to honour it:

- **A wiring gap counted as a model fault.** The revisitor's `_asked` map was a
  bare attribute; `dream_loop`'s running lock is per-`(adaptor, key)` and the
  live `policy.toml` has two units, so a sibling tick wiped the map
  mid-flight and a grounded verdict was dropped as `reason="unknown_note"` — a
  model answering about a note it was never shown. It was a lock scope.
  (`b661f83` F3, branch `worktree-note-revisitor`.)
- **`unavailable` counted as `absent` as well, on the same candidate.** A
  failed record read left the evidence tuple empty, and the branch below read
  an empty tuple as «the room has said nothing since the note» — so one
  candidate incremented two states and the tick reported 2 for 1. Fixed by
  carrying `evidence_state` as ONE value; the module's own comment records that
  the defect was «committed inside its own instrument»
  (`92eccc7`, then in `nora/language/note_revisitor.py`; that module merged
  into `nora/language/note_curator.py::_gather_evidence` on 2026-09-06; that
  module itself was deleted 2026-10-03, DEC-277).
- **A tick that wrote nothing reporting `ok`.** `dream_screen` matches any
  hyphenated UUID (R-18/R-28) and note ids are `uuid4`, so every curator item
  was rejected: the tick fired, spent a model call, `accept` received nothing,
  `commit` wrote nothing, and the run reported `ok` because the ENVELOPE was
  non-empty. Silent, and every test in the file missed it because the double's
  ids are `note-1`. (`e5c5bea`.) The mirror image landed the same night — an
  over-cap merged body raised out of `commit` and reported
  `unavailable`/`written=0` over rows that exist in Postgres (`6d23a41` P2).

A wiring gap is `unavailable`. Never `nothing_found`, never `ok`, and never two
states for one event — the third is the one that is hard to see, because both
series look plausible on their own.

### A declared metric with no call site is a lie

An UNdeclared metric is a `KeyError` at the first increment. A declared one
with no increment renders its `# HELP`/`# TYPE` header and nothing else, which
on a dashboard is a series that is genuinely always zero: the reading «this
never happens», not «this is not wired».

Two on master then — `nora_fact_candidates_total` and
`nora_fact_entity_unresolved_total` in `nora/obs/metric_specs.py`. *(Both went
with the knowledge tier when it was deprecated and deleted on 2026-09-07, issue
#115 — the instance stands as the war-story, and the rule it earned is
unchanged.)* The second was named in the note tier's own block as «the exact
defect this block was written to close» — the argument for shipping three note
counters instead of the ten that tier's spec names — while it sat unwired
itself. `nora_store_busy_total` scans the same way and is NOT an instance: its call site was deliberately deleted with the SQLite WAL
apparatus (DEC-51), and the name is retained because names are append-only.

The declared-metric gate exists:
`tests/architecture/test_every_recorded_metric_is_declared.py` checks recorded
names against declarations. A call site whose name it cannot read statically
must be pinned there, never skipped. This checks increments have declarations;
the reverse question, a declared name with no call site, still needs the scan
over `nora/obs/metric_specs.py` and `metrics.inc(` (issue #56).

### Label vocabularies are derived, never hand-copied

`EVIDENCE_STATES` was a hand-written tuple nothing referenced, while
`_candidate` assigned bare string literals. **Emitting a fifth state left every
test green** — so the parity test, written to catch «a value in the code the
spec does not name», could only ever catch DELETION from the help text. The
exact drift it existed to prevent went through it unseen: `self_only` was added
to the code and never to the spec, so the endpoint emitted four values while
the documentation described three, for two review rounds. The fix for a
silent-failure finding had the silent failure's shape.

It is now `EvidenceState = Literal[...]` with `EVIDENCE_STATES =
get_args(EvidenceState)`, enforced TWICE — statically, and at runtime by
`NoteCuratorAdaptor._inc`'s `_VOCABULARIES` check *(adaptor deleted 2026-10-03, DEC-277; the pattern stands)* (every increment's label is
checked against the derived set before the counter is touched) — because
`nora/language/` was then outside the mypy scope (issue #61); it is now included
in `make typecheck` and **a static-only guard in an unchecked package is a
claim rather than a check** (`443bbca` F3).

Where mypy does reach, the derivation alone is enough: `nora/domain/facts.py`
built five frozensets with `get_args` off five `Literal`s *(the five went with
the knowledge tier on 2026-09-07, issue #115; the file survives as the two DDL
ceilings the not-dropped `fact` tables read; the pattern is still the rule, and
`nora/language/note_curator.py` (deleted 2026-10-03, DEC-277) still carries it)*. Where it
does not, the vocabulary is a frozenset the code increments AGAINST and a value
outside it raises — a label typo forks a NEW series rather than erroring, and
the old one silently goes flat (`nora/language/note_curator.py`,
`_VOCABULARIES`, `6d23a41`).

A derivation check needs its own positive control, and the walk that reads the
emitted labels out of the source asserts `emitted, "the walk found no reasons
at all — it is not measuring"` before comparing sets. Its first version matched
`reason="..."` with a regex and flagged a string inside a COMMENT — a matcher
that matches too much, reproduced inside the test written to prevent drift.

### Pair every zero-assertion with a positive control

`assert metrics.get(x) == 0` is true of a stage that raised. `Metrics.get` also
sums matching series and answers `0.0` when there are none, so on its own it
cannot separate «published zero» from «never published» either.

`tests/unit/test_debrief.py::test_the_first_fold_of_a_conversation_is_not_a_
rederivation` asserted `nora_standing_rederivations_total == 0` and
`nora_debrief_prior_total{prior="absent"} == 1`. Remove `if prior is None:
return None` from `_should_rebuild` and the stage raises `AttributeError` on
`NoneType.fold_count`, writes nothing, lands `outcome="error"` — and BOTH
assertions still hold. R-72 named that test among the ones enforcing it, and it
measured nothing. It was found by a reviewer mutating a clause the author had
not. (R-72 is retired and the test gone with `_should_rebuild` itself, deleted
2026-09-24 with the grounding verdict, `design/standing-compact/`; the lesson
stands.)

At least five tests in that one file had the shape. The fix in each is two
lines — `outcome="written" == 1` and a read-back of the row that was actually
written (:722-724, :767-772). Presence and magnitude are two different
questions, and a test whose assertions are all true of a crash is not a test.

The general form is the bundle's own: **a test that has never been seen red is
a claim, not a measurement.** After a test goes green, break the production
line it names and watch it go red — per CLAUSE, not per change. One author
truthfully reported falsifying three properties of one feature, and the fourth
clause was the one that shipped the defect.

## The five operational constraints (spec §2, with corrections)

These came from `design/08-stack-refactor-brief.md` and are preserved here
because the spec that retired that document said they were the most
operationally valuable text in it. Losing any of these wastes an afternoon.

1. Direct builds require `docker build --network=host` — BuildKit's default
   bridge cannot resolve `deb.debian.org` on this host (systemd-resolved stub
   `127.0.0.53`). Every compose `build:` sets `network: host`, so
   `docker compose build` needs no flag (`nora-deploy` reference notes).
2. Honcho health uses **`GET /health`**, not `GET /v3/workspaces` (POST-only →
   405 → forever `health: starting`).
3. **signal-cli is Nora's transport and runs in her compose project.**
   *Corrected 2026-08-05.* This constraint used to read "neutral and shared —
   never touched", which was true when the legacy Hermes gateway used it. It is
   no longer: signal-cli *binds* `:18080` and Nora was its only *client* (`ss -tlnp` shows
   java listening, `python3 -m nora` merely connecting), and
   hermes-agent runs profiles `attest`/`bento`/`default` with no `nora` profile.
   The daemon now lives in `docker-compose.yml` alongside Nora, built from
   `deploy/signal-cli/` (upstream ships **no** official image, so `fetch.sh`
   downloads and GPG-verifies the release tarball against AsamK's key
   `FA10826A74907F9EC6BBB7FC2BA2CD21B5B09570`).

   **`--receive-mode=on-start` is load-bearing — never drop it.** Without that
   flag signal-cli starts its HTTP server and answers every RPC perfectly while
   **never opening its websocket to Signal**, so not a single message is
   delivered. The host daemon ran that way for 61 days: `listGroups`,
   `listContacts` and `sendSyncRequest` all worked, `/health` was green, and
   `nora_messages_total` sat at `0.0` the entire time. Under R-5 Nora is silent
   when broken, so it was indistinguishable from a quiet group.

   It needs **Java 25** (class file version 69). Temurin 21 fails at class load.

   If group state goes stale on a linked device, `sendSyncRequest` over
   `/api/v1/rpc` asks the primary to re-send it; this recovered a known group
   after `listGroups` had returned `[]`.
4. The legacy Hermes gateway is retired. If migrating an old installation,
   archive its profile outside the checkout; do not reuse the old profile path.
5. Dependencies are added with **`uv add`**, never raw pip.

## Conventions (spec §10 — the short version; see the spec for the rest)

- **One stage, one module, one public entry point.** This is about
  RESPONSIBILITIES, not lines. A 400-line module doing one thing is fine; a
  150-line module doing five is not.

  There used to be a mechanical 300-line cap enforced in CI (spec §10),
  calibrated on `handler.py` "going wrong at 229 lines with 8
  responsibilities". It was deleted 2026-08-06. The calibration confused the
  symptom for the cause: `handler.py` was bad because of the eight
  responsibilities, and a line count cannot see those. In practice the gate
  made things worse — it was satisfied twice by DELETING explanatory comments
  from `nora/agent/guards.py` rather than by changing any structure, which is
  the opposite of what it existed to encourage, and it blocked
  `nora/domain/ports.py` (pure Protocol declarations, nothing to split) from
  gaining a method. Its own docstring conceded that file was "legitimately
  dense with no behaviour to split out" and enforced the cap on it regardless.

  If a module feels too big, count what it is responsible for.
- **Comments describe, git records.** A comment says what the code or key IS
  and the constraint a reader cannot see from it: units, bounds, the default,
  what it requires, when to change it. It is never a log: no dates, issue or PR
  numbers, measurements, review stories or "was X, now Y" — the commit, the
  issue and the ADR hold those, and a comment copy drifts from them. A decision
  is cited as a bare pointer (`DEC-123`, `ADR 0023`), never retold. The same
  holds for docstrings, including a test's: it says what the test guards, not
  how the bug was found. Gated on these markers, never on length: example TOMLs
  by `tests/architecture/test_example_toml_comments_are_reference.py`, Python
  by `tests/architecture/test_comments_carry_no_new_log.py` (dates and issue
  numbers; the baseline in `log_marker_baseline.json` only goes down, and
  `python scripts/log_markers.py --write` records a cleanup). A model-facing
  docstring (a tool's) is behaviour: change it through the nora-tool skill,
  not as a comment edit.
- Test-seam policy: production classes take collaborators through `__init__`
  only — no `*_fn` hooks, no `_for_test` params, no module-level mutable
  singletons.
- A port never gains a second implementation — including a test fake —
  without a shared contract suite both pass (`tests/contract/`).
- A new behavioural rule gets an entry in `design/09-decision-index.md` and a
  named test registered in `tests/test_acceptance_rules.py::RULE_TESTS`.
- Where a test LIVES, what config it LOADS and where its FAKES live are the
  three rules of the next section (issue #113), each with its own gate under
  `tests/architecture/`.

### Contract database scope

- **A contract suite scopes its database to the MODULE, not the test.** The
  shared `pg_url` fixture is function-scoped: `CREATE DATABASE` + the full
  migration chain + `DROP`, every test. At ~30 database-backed tests that
  degrades the container enough to fail an unrelated wall-clock test in another
  directory — the cost lands nowhere near its cause. Build once per file and
  `TRUNCATE` between tests; the isolation these suites need is "no rows from
  the last test", not "no schema from the last test".

## Test structure — where a test lives, what it loads, where its fakes live

Filed as issue #113 (2026-09-07) from the operator's own words: *"our test
cases and testing methodologies have no convention, no practice, no policy,
and no standard."* Measured, not estimated: 324 test files across 8
locations; **zero** documents defining what any test directory meant; the
largest bucket the unnamed `tests/` root (144 files, 45%); **seven** distinct
ways a test acquired a `Config`; 11 shared fakes in `nora/testing/` against
**86** test files defining their own fakes inline.

The cause was precise. The three test rules this file already carried — a fake
must behave like the real server, collaborators come through `__init__`, a
port's second implementation needs a contract suite — all govern whether a test
is HONEST. None governed where a test lives or where it gets its inputs. The
quality bar was high and the skeleton was absent. That gap was already costing
correctness: the port-fake rule was being broken silently in dozens of files,
because a fake defined inside a test module is invisible to every contract
suite by construction, and nothing compared it to production.

Three rules, each shipped with its gate — a rule with no test is a claim.

### R-A. A test directory names what makes the test TRUE, not what it is about

| directory | what makes a test here true |
|---|---|
| `tests/unit/` | pure logic — no I/O, no container, no network |
| `tests/contract/` | ONE suite, N implementations of a port (fake AND production) |
| `tests/integration/` | the wired pipeline, real Postgres, real HTTP servers |
| `tests/infra/` | a real adapter against its real backend (Postgres, rustfs, docker) |
| `tests/architecture/` | a property of the SOURCE, read off the AST, not the runtime |
| `tests/web/` | the aiohttp control-plane surface |
| `tests/domain/` | legacy subject-named bucket — `unit/` for new tests |
| `tests/` root | **legacy** — holds no NEW test files; the 140-odd there move under R-A |

`domain/`, `infra/` and `web/` are *subject*-named, a second taxonomy cutting
across the first, and that is why 45% of files landed in the root: with two
axes and no rule, the path of least resistance was neither. The taxonomy is
written here so the next test has a home; **the move itself is deferred** (the
largest diff and the least urgent) and tracked on #113. The gate that comes
with the move — *a test collecting a container fixture outside `integration/`
or `infra/` fails* — lands with it, not before, because today `contract/`
legitimately collects `pg_url` for its SQL implementations.

### R-B. A test gets its config from the TEMPLATE; only a test whose subject IS the deployment reads the real file

Two fixtures in `tests/conftest.py`, both built on
`tests.builders.copy_example_config` — the one place in the suite that knows the
template's name:

- `example_config_path` — a copy of `config.example.toml` in `tmp_path`, for a
  test that edits the TOML before loading it.
- `example_config` — that copy, loaded through the production loader with the
  LLM-key placeholder set (the load is fail-closed on the key, as startup is).

Seven acquisition patterns collapsed to two. The war-story that named the rule:
four files loaded the eval harness's config through `load_eval_config((real,
example))`, which prefers the DEPLOYMENT's file, so unit tests of `_build_sink`
asserted the operator's `config.toml` — and went red the day the operator
enabled the Langfuse export. A false dependency, recorded in
that gate test's own docstring, which is now the fixture's. (That file was
split on 2026-09-18: its evaluator assertions live in
`tests/unit/test_the_deterministic_evaluators.py` and the rest went with the
old eval layer.)

The three files that DO read the real `config.toml` cannot take the fixture,
and are pinned with the reason: `tests/builders.py` (the copier),
`tests/test_env_template.py` (the F11 drift gate — pointed at the template it
would compare the template to itself, green forever, measuring nothing) and
`tests/test_turn_budget.py` (R-24 asserts the deployment's budgets against
BOTH files because a fix applied to one is half a fix).

**Gate:** `tests/architecture/test_config_comes_from_the_template.py`. Reads
the AST, not a grep: `<repo root> / "config.toml"`, `Path("config.toml")`, an
import of `load_eval_config`, or a module-level constant naming either file,
outside the pinned three, fails. A message string mentioning the file does
not. Every pinned file must still show a read — the positive control and the
stale-pin check in one.

**Side benefit:** the "should `config.toml` be tracked in git?" question that
started the discussion dissolves — the coupling to the deployment file is now
in one place with a stated reason, instead of four.

### R-C. A fake standing in for a port lives in `nora/testing/` and passes that port's contract suite

Inline classes are for local scaffolding only. «Standing in for a port» is
read off the source: a class in `tests/` that defines EVERY public method of a
`Protocol` in `nora/domain/ports.py`, each with EXACTLY the port's parameter
names (or a `*args`/`**kwargs` wildcard). Exact, not a superset — a
`describe(data, media_type, prompt, model, provider)` double is a
`VisionClient`, not a `MediaInterpreter`, and a one-method port must not claim
every class with a same-named method. A noisy gate is a disabled gate.

Three structural exemptions, none by name: a class inside `tests/contract/`
(under the suite by construction); a double whose port methods only FAIL (a
bare `raise`, or a `sleep` stall then a raise — it implements nothing, so it
cannot drift); and a subclass of a `nora.testing` fake (the contract-bound
fake with one local twist).

**Gate:** `tests/architecture/test_port_fakes_live_in_nora_testing.py`.
Everything else is pinned in `_ALLOWED` with one of three reasons —
`NO_SHARED_FAKE`, `DRIVES_THE_LOOP`, `FAULT_SWITCH_OVER_SHARED` — and the pin
is checked BOTH ways: an unpinned stand-in fails, and a pin whose class no
longer stands in fails too. The list is an inventory to burn down, never a
place to add to. The positive control runs the matcher on `nora/testing/fakes.py`
and must see the shared fakes it points at.

**What the first landing found — the issue predicted "red tests that were
green", and it was right:**

- `tests/fakes.py::FakeHoncho` — 24 users, never under the memory-store
  contract suite — had become a byte-for-byte behavioural copy of
  `nora.testing.FakeMemoryStore`. Deleted; every user now imports the
  contract-bound one.
- `MediaObjectStore` had a SQL store and NO shared fake, so two files wrote
  their own with DIFFERENT search semantics (one substring-matched
  description/prompt/title, one returned every row). Writing the shared
  `FakeMediaObjectStore` and its contract suite against
  `SqlMediaObjectStore` turned one test red **against production**: the SQL
  `search` combined words with `or_` while its own docstring said it ANDed
  them — "birthday card example-person" returned every birthday card. Fixed in the
  store. No offline test could have found it, because every offline test ran
  against a fake that invented its own semantics.
- Three files each carried a one-method `ReverseGeocoder` fake; now
  `FakeReverseGeocoder`, held to the port's shape in the geocoder suite beside
  the Google and HK-gov backends.
- The blob, roster and description-store copies in the tool tests were
  replaced by the shared fakes — one of which had to learn to record the
  `model` it was handed, because a test needed it and the inline copy had
  been written instead of teaching the shared one.

**Not done, and pinned rather than hidden:** 10 inline stand-ins remain in
`_ALLOWED` (`tests/architecture/test_port_fakes_live_in_nora_testing.py`, the
list only shrinks), most for ports with no shared fake yet
(`SearchProvider`/`GifSource`/`SocialProvider`, `TrackSource`/`MusicToolBridge`,
`DreamAdaptor`/`DreamSink`) and four that steer `dream_loop`'s supervision
rather than model a port. Each is a candidate; promoting one means a
`nora/testing/` fake plus a contract suite against the production
implementation, which is the work the rule exists to force.

**Environment finding, recorded because it hid the suite from the operator's
own laptop:** SQLAlchemy's async engine needs `greenlet`, and SQLAlchemy's own
dependency marker skips macOS arm64, so on an Apple Silicon machine every
SQL-touching test errored with "the greenlet library is required". The
container is Linux and never saw it. `greenlet` is now declared directly.

## Local comments and configuration sources

Keep comments short and descriptive. Maintained docs own architecture, rationale
and decisions. The [configuration contract](../configuration.md) amends the old
environment-only credential-source guidance; `*_env` references retain their names.
Private `secret.toml` and generated delivery files never enter Git. Config and
secret changes require validated controlled recreation; identity/policy retain
their existing validated reload.
