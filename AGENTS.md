# AGENTS.md

**The entry point for any agent working in this repo** — Claude Code, Codex,
Cursor, or anything else that reads an [`AGENTS.md`](https://agents.md). It is
vendor-neutral on purpose: `CLAUDE.md` is a pointer at this file, not a second
copy, because two copies drift and the one that drifts is always the one you
did not read.

This file is the terse, scannable rule set. The reasoning, the war-stories, and
the specific values behind these rules live in **`docs/CONVENTIONS.md`** — read
it when a rule needs its "why"; every terse rule below is only safe because that
detail is preserved. **`CONTRIBUTING.md`** carries the branch, commit, PR and
issue conventions.

## The spine: comprehend → measure → serialize

Every path in this repo already runs this loop; it is named here so new work — a
tool, a stage, an **eval** — keeps to it rather than reinventing a weaker shape.
It is a **constraint, not a procedure**: not *how* to build a thing, but what
every step must preserve. (This is UACP's CMS applied to Nora; the three verbs
are the same everywhere the codebase reasons, decides, or commits state.)

- **Comprehend** — turn raw input into a *computable model*, and interpret it
  **once**. A Signal message becomes a `NormalizedMessage` → `TagContext` (tagger
  resolved, transcript, peer card, the prefill blocks); everything downstream
  computes on that fixed model and never re-parses the raw text. Interpreting
  twice is how two copies drift.
- **Measure** — reduce the model to a **decidable signal**: grounded, and
  **fail-closed**. This repo's signature is the R-27 three-state tool result —
  `ok` / `nothing_found` / `unavailable` — where an ERROR is never a PASS: a
  wiring gap is `unavailable` («I couldn't check»), never `nothing_found`
  («there's nothing»), and a stale/absent fix is `unavailable`, never a guessed
  point. The guards are the *negative* measurement — R-5 (silence beats a wrong
  answer), R-18/R-28 (no identifier or coordinate egresses), the disclosure
  subset (R-31 fails closed on any unknown in the room). **Bind to the real
  property** — a `where_is` fix is a live GPS read, a `weather` reading is HKO's
  own — never a proxy, and never assert what you cannot ground.

  **When the measurer is a MODEL, its input has to explain itself.** A block
  handed to a model is not evidence because it is present; it is evidence when
  the model can tell what it is, how old it is, and which way it reads. Every
  question you ask the model, ask of the block first: if the judgement turns on
  *when*, the block carries a time; on *who*, it carries an index; on *whether
  this is still open*, it carries the thing that would close it. A judgement
  whose input cannot ground it does not fail loudly — the model answers anyway,
  and it answers wrong at a rate nothing reports.

  **So when a model judges badly, read its input before you rewrite its
  instruction.** Measured three times over on 2026-09-09/10 and it went the
  same way each time: the Relevance Gate was asked whether a room had moved on,
  was still waiting, or had been answered — three questions about time — from a
  transcript with no timestamps and no statement of which end was newest. Three
  rewrites of its instruction changed nothing or made it worse, one of them
  breaching a safety bar. Rendering an age per row cut its error rate by two
  thirds over 40 labelled cases. The same omission was then found in
  `<standing>`, which asserted it was current while carrying no time, and in the
  note curator, which is asked whether an anchor has passed and is given no
  clock at all. The instruction is the first thing blamed and usually the wrong
  one.
- **Serialize** — fix the result into **canonical, typed state with provenance**.
  Her reply is the one authored surface (strict-silence: only a `send_reply` tool
  call delivers; the never-silent fallback is delivered but explicitly kept out
  of her authored `sent_texts`). The audit rows and the Honcho feed carry her
  words under the Nora aiPeer with their `doctrine_sha256`/`tag_id`; the Langfuse
  trace is the provenance every claim about a turn re-derives from.

**The three rules that make it hold** (UACP's, and they are load-bearing here):

1. **The discipline IS the engineering.** A `measure` that isn't fail-closed, or
   a `serialize` without provenance, is *decoration* — the narrative reads
   systematic while the result rots back into re-judgment.
2. **No self-attestation.** "Done" is never decided by the doer — it is
   **measured against evidence** (a green gate, a moved metric, a read-back
   trace) judged by something else. This is exactly the "Never claim a merge,
   deploy or fix without reading back the state" rule below.
3. **Re-derivable, so no actor is trusted.** Every step binds to reality and
   carries provenance, so a turn — or an eval verdict — can be reconstructed and
   checked *without trusting the producer*.

**Apply it at every grain** — the whole turn AND each sub-step, down to one tool
call. `serialize(N)` is the input you `comprehend(N+1)`. The **eval layer is this
same loop one level up**: comprehend a real user-journey into a typed case,
measure the LIVE model's trajectory fail-closed (empty reply / wrong path /
budget blown = FAIL; timeout = ERROR ≠ PASS), serialize a verdict bound to its
Langfuse trace. Same three verbs, same mindset — that is how a new capability's
evaluation reuses this framework instead of growing a bespoke one.

## Reporting to the operator

**Two registers, and using the wrong one is the common failure.**

**THINKING** — architecture, brainstorming, design trade-offs, *why is this
wrong*. **Full depth. Take the room you need.** Compressing an architectural
argument damages it, and terse reporting must never bleed into design
discussion.

**REPORTING** — work finished, a check ran, a peer replied. Terse:

```
<the result, first line, no preamble>

STATUS      what is true now
DECIDED     what changed — one line each
NEEDS YOU   only what genuinely requires the operator, with the real trade-off
```

- **Headers are subjects, not verdicts** — *"notes never called"*, not *"three
  things I got wrong"*.
- **Lead with the conclusion.** If the most important fact is in paragraph five,
  the report failed.
- **No internal identifiers in prose unless asked** — no section numbers, metric
  names, instance counts. The operator should not have to look something up to
  parse a sentence.
- **Reasoning goes in the artifact, not the message.** Design nodes carry
  provenance because they must be re-derivable; a status report must not.
  Applying the spine's provenance rule to *conversation* is a category error —
  it is the single most common way these reports go wrong.
- **Errors get one clause, then move on.** Never a paragraph on how you found it,
  never a tally of past mistakes.

## Decisions change on EVIDENCE, not on argument

When a peer, a reviewer or the operator pushes against something already decided:

1. **Is there new evidence** — a measurement, a read-back, an executed probe, a
   file that says otherwise?
2. **Yes** → change it, state the evidence in one line, do not narrate the
   reversal.
3. **No** → **hold the decision.** Say what it rests on and what reading would
   overturn it. A better-argued restatement of a known position is not new
   information.

**Deference-to-recency is the failure**: treating the most recent confident input
as authoritative. It reads as responsiveness and is the absence of a position.
2026-08-27: one session flipped three times on the same build-vs-vendor question
with **no new measurement between the flips** — which means it never had a
decision, only a preference.

**Write the reversal condition when you decide.** *"This holds unless X"* — the
bundle already does this for design nodes (*"the reading that would reopen it"*);
it applies to any position you state. It makes the next challenge cheap to answer
and makes a flip visible as a flip.

**A decision the operator has locked is closed.** Surface new reasoning; leave
the decision.

## Reading order

1. `README.md` — what Nora is, how to run her.
2. `docs/CONVENTIONS.md` — the detail behind this file: gate reasoning, the "Never"
   war-stories, the five operational constraints in full, the conventions.
3. `ARCHITECTURE.md` — the map: topology, package table, ports, a walk of one
   ambient message and one tag.
4. `design/09-decision-index.md` — one line per decision, pointing at the
   document that made it and the test that enforces it.
5. `design/*.md` — the topology documents the decision index points into.
6. `CONTRIBUTING.md` — branch, commit, PR and issue conventions, and the
   issue → ADR → decision-row flow.
7. `docs/adr/` — Nygard-form ADRs: why a decision was made and what it cost.

**A `docs/superpowers/…` path is a historical citation, not a location.** That
tree — 34 dated specs and 35 plans — was **removed 2026-09-08** along with
`.superpowers/`; the process it named is gone and is not coming back. The paths
still appear in design nodes, docstrings and config comments as provenance for
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
curator only retires; the create gate in `note_candidates` matches on anchor
date alone. R-75 in the decision index says which clauses survive.

## The three config files

Three files, three lifetimes, deliberately not merged (DEC-8's argument):

| file | answers | changes when |
|---|---|---|
| `config.toml` | ports, models, timeouts | the deployment changes |
| `identity.toml` | **who people are** | a human joins |
| `policy.toml` | **who may talk to her, and where** | trust changes |

**All three are gitignored now** — `config.toml` joined the other two on
2026-09-07 (operator decision). It was tracked on the argument that it holds no
secret values, and what made that untenable was collision rather than secrecy:
the live file is a bind-mount source the operator edits in place, so every
merge touching it fought an uncommitted working change and each session had to
stash-merge-restore-diff by hand to keep from losing an edit. `config.example.
toml` is now the only committed record of the config's shape, and
`tests/test_env_template.py` is what holds the two in step — that gate SKIPS
where there is no local `config.toml` (a fresh clone) and runs for real on the
deployment machine, which is the one whose drift would stop a container
booting under `extra = "forbid"`.

**Config mutation goes through the CLI, not a hand edit** (deploy convention
§4.5, implemented 2026-09-08): `nora config set/get/unset/validate/path --config
<path>`. The subcommand validates against the schema, writes atomically
(tmp + rename), prints what changed, and never touches `.env` — secrets stay
there and are referenced by name. Hand-editing a 75KB TOML remains possible
(`extra = "forbid"` still catches a broken file at boot) but is the last
resort, because it bypasses the validation and the audit trail the CLI gives.

The last two are bind-mounted read-only and **loaded once at
startup** — editing either without a restart changes nothing and warns you about
nothing (`nora_unmapped_senders_total` is the tell). `identity.toml` takes one
`[[persons.<id>.<platform>]]` block **per account**, all resolving to one
`person_id` (their Honcho peer, A2/DEC-14). `policy.toml` is deny-by-default and
Nora **refuses to start** if it is missing or allows nothing (R-5).

## Reaching the deployment

Nora runs on the `norty` host; development happens on the MacBook. The host is
a **deploy target, not a dev checkout** — nothing is edited there, and no
session runs there. Every read-back the "Never claim without reading back"
rule demands is one `ssh` away:

```bash
ssh norty 'cd ~/workspace/nora && git log --oneline -1'          # what is checked out
ssh norty 'cd ~/workspace/nora && docker compose ps'              # what is running
ssh norty 'cd ~/workspace/nora && docker compose exec -T db psql -U nora -d nora -Atc "select count(*) from note"'
ssh norty 'curl -s localhost:9100/metrics | grep ^nora_dream_'    # the metric that proves it
ssh norty 'cd ~/workspace/nora && awk "/^\[notes\]/,/^\[/" config.toml'   # the LIVE config, untracked
```

- **Key auth only.** `~/.ssh/norty` is a dedicated ed25519 key, installed once
  with `ssh-copy-id`; `Host norty` in `~/.ssh/config` binds user, host and key.
  **Never `sshpass`, never a password in `.env`, in chat, or in a file a session
  reads** — a password typed into a session is transcribed into memory (this
  happened once), and `.env` is the compose environment on both machines.
- **`config.toml` on the host is the only copy that matters.** It is untracked
  since 2026-09-07; the worktree's copy, if any, is stale. Read the live file
  before asserting what a flag is set to.
- **The db container is reached through compose** (`docker compose exec -T db`),
  never by grepping `docker ps` for a name — Komodo's own databases live on the
  same host and match the same patterns.
- **Container logs ARE on the docker log driver, since 2026-09-09** — `docker
  compose logs nora` gives JSON lines; pipe it through `jq`. It was a file
  (`data/nora.log`) for five weeks because this module landed one day before
  the Dockerfile, and «`docker compose logs nora` is empty» was documented as
  a rule in three places instead of fixed. Anything still telling you to grep
  `data/nora.log` is stale. The metrics endpoint and the tables remain the
  stronger evidence for whether a background pass RAN.

## Gate commands

**While iterating**, use the fast loop — it is what the edit-run-edit cycle is for:

```bash
make test-affected     # selects targets from what changed; -n 8; falls back to the full parallel suite
make test-fast         # the whole suite, -n 8
```

**Before claiming anything is done, and before every commit**, run the gate. It is
deliberately SERIAL and `make test-fast` does not replace it (see the Makefile's
own note):

```bash
make gate              # all four, in order — or run them individually:

uv run pytest -q                                         # tests green
uv run ruff check nora/ evals/ tests/                    # lint clean
uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence nora/engagement   # types clean (see the scope note)
uv run lint-imports                                      # import boundaries clean
```

**What "types clean" actually covers.** The strict flags in `pyproject.toml`
apply to `nora.domain`, `nora.config`, `nora.app`, `nora.infra` and
`nora.presence` — five packages. `nora/engagement` is on the mypy command line
but NOT in that override, so it is checked without the strict flags; measured
2026-09-08, it carries **137 errors** under them. The rest of `nora/` is not
type-checked at all, and the measured strict-error counts are: `web` 156,
`ingest` 137, `agent` 133, `analytics` 110, `compute` 66, `platform` 34,
`testing` 30, `obs` 11, `doctrine` 6, `instructions` 5, `language` 3, and
`skills` 0. (`nora/dreamer` holds no Python at all — it is the objectives tree —
so its absence from the scope was never a gap.) Do not read a green `make gate`
as "this repo is type-safe"; read it as "five packages are, and the numbers
above say what the rest would cost". Issue #121 tracks the expansion.

A fresh worktree needs `uv sync --extra evals` once, or pytest dies at
COLLECTION on the eval modules — `strands-agents-evals` is a declared optional
extra and each worktree has its own venv.

The gate is the FINAL check, not the iteration check. It takes ~8 minutes; running
it after every edit instead of `make test-affected` costs an hour a day and buys
nothing — this file previously named only the serial form, and that is exactly the
mistake it produced (2026-08-21: eight full gate runs in one session).

## Release fragments

Every branch with a user-observable change adds a JSON fragment under
`release-fragments/unreleased/`: features, fixes, behaviour/security changes,
and meaningful limitations all count. Internal-only work adds a `type: "none"`
fragment with a reason. The fragment is the source for the next version tag's
annotation; do not reconstruct product notes from commit subjects at release
time. Validate the branch before merge:

```bash
uv run python scripts/check_release_fragments.py --base origin/master
uv run python scripts/render_release_notes.py
```

See `release-fragments/README.md` for the schema. Fragments accumulate across
ordinary deployments and never create a tag or deploy a release themselves.

The schema gate is `tests/infra/test_schema_parity.py`, **not** `alembic check`
(which is blind to the CHECK constraint and the `ts DESC` index ordering); both
run in `pytest`. There is no module-size gate (deleted 2026-08-06). See
`docs/CONVENTIONS.md` for the DEC-11 story, the `lint-imports` cache note, and why
the size gate went.

## Design backlog

Not-yet-built design ideas discussed in a session get filed as a **GitHub issue**
on `mikeng-io/nora` (not left only in chat/memory) and tracked there — comments,
scoping, status all live on the issue, not scattered across sessions. Only a
decision that actually lands gets a `design/09-decision-index.md` entry; an open
issue is a candidate, not a decision. Example: #4 (per-conversation rolling
summary — update-in-place, separate from the Dreamer and Honcho).

## Never (one line each — the war-story behind each is in `docs/CONVENTIONS.md`)

- **Never `pip install`** — dependencies via `uv add` (operational constraint 5).
- **Never write a test whose fake answers every request the same way** — make the
  fake behave like the real server, including its wrong-call shapes. If reverting
  the code under test doesn't turn the test red, the test is decorative.
- **Never point signal-cli at a different account, re-register it, or unlink its
  device** without an explicit decision — that store holds the device
  registration. Routine reads/RPC are ordinary; destructive account ops are not.
- **Never reference the archived legacy path** (`~/.hermes/profiles/nora/`).
- **Never assert a per-tag object was cleaned up by checking an attribute was
  deleted** — count live `gc`-tracked objects instead.
- **Never rewrite a model's instruction before reading its input** — if the
  judgement turns on *when*, check the block carries a time; on *who*, an
  index; on *still open*, the thing that would close it. Three instruction
  rewrites in one night changed nothing or made it worse; rendering an age per
  transcript row cut the same gate's error rate by two thirds.
- **Never paste production rows into a fixture** — not a slice, not "just six
  people", not with a docstring explaining that it is real. `SELECT`ing out of
  `nora-db-1` produces PII that outlives the branch, the test, and the decision
  to delete it: `tests/fixtures/presence_corpus_sample.py` held 11,431 real GPS
  fixes for six named people, and parsed back, every one of them has a
  residence recoverable to an 11-metre cell from overnight fixes. It was
  removed from the TREE in one commit and stayed reachable from `master` and
  `origin/master` for three weeks, because deleting a file is not deleting an
  object. Fixtures are GENERATED, or they are placeholders from
  `identity.example.toml`. If a synthetic corpus cannot reproduce the property
  under test, that is a finding about the property, not a licence to commit the
  real one. `tests/architecture/test_no_real_identifiers_in_the_tree.py` is the
  gate: every phone number, service id, platform snowflake and private address
  in the tree must be DECLARED there, and its `_PENDING_REAL_*` inventories
  only shrink.
- **Never read an instrument's output as evidence without first checking it can
  measure the thing** — state what a positive result would look like before
  concluding from a null one.
- **Never claim a merge, deploy or fix without reading back the state you claim**
  — `git log -1`, `docker exec … import config`, the metric endpoint. Every time.
  Pointed at a test suite instead of a deployment, this is the mutation rule: a
  fix is done when reverting the exact production line turns a NAMED test red,
  not when the gate is green. `CONTRIBUTING.md` «Proving a fix» carries it, with
  the two harness rules that otherwise turn "caught" into a lie.
- **Never edit, commit, or merge on `master` in this checkout** — every change,
  including a one-line doc fix, starts in a worktree on its own branch and
  reaches `master` by merge, so the merge is the moment somebody decides rather
  than a side effect of editing. **The bind-mount source is `norty`'s own
  checkout (`~/workspace/nora`), not this one** — read back 2026-09-08: the
  compose bind sources are relative to the compose file on the host, so a merge
  here reaches the container only when `norty` pulls. An older topology, in
  which the dev checkout WAS the bind-mount source, is what the previous
  wording described; it is superseded, and it mattered in both directions — it
  made one session sequence a deploy around a window that did not exist, and
  read while standing on `norty` it points at the wrong machine. What is
  unchanged: on `norty`'s checkout an edit to `nora/doctrine` IS a deploy (it
  hot-reloads on mtime), and `config.toml` there is live at the next restart.
  **Landing a merge does not move you back
  in** — the next change starts in a new worktree too. 2026-08-21: a session
  merged, kept editing in place, and another session was seconds from building
  an image out of a mid-refactor tree with 16 tests red in it.
- **Never run two agents against this checkout at once** — sequence them, or give
  each a worktree.
- **Never trust `alembic revision --autogenerate` for `idx_messages_conv_ts`** —
  it is excluded on purpose; a revision touching it is hand-written.
- **Never assume your migration's parent is unclaimed** — two branches cut from
  the same head each write `down_revision = <that head>`, git merges both files
  without a conflict, every test that does not actually migrate stays green, and
  the container fails at boot because `upgrade "head"` refuses two heads.
  **`uv run alembic heads` must print exactly ONE line after every merge.**
- **Never take a DEC or R number from a chat agreement** — re-read
  `design/09-decision-index.md` at MERGE time, not at claim time. 2026-08-21:
  two sessions coordinated numbers carefully and a third took DEC-131 anyway;
  it conflicted only because the rows landed on the same line. Check with
  `grep -oE '^\| DEC-[0-9]+ \|' design/09-decision-index.md | grep -oE '[0-9]+' |
  sort -n | uniq -d` (and the same with `R-`) — note `uniq -d` exits 0 with no
  output, so test the string, not the exit code. **The trailing `\|` is
  load-bearing**: sub-lettered rules (`R-6b`, `R-10c`) otherwise read as their
  parent number and the check reports 8 duplicates that are not. 2026-08-23: a
  REAL duplicate — two rows numbered R-29 — sat on master for 15 days, reported
  and backlogged by the session that found it, because the check that should
  have caught it cried wolf seven times. A noisy gate is a disabled gate.

## Instruments and spans (one line each — war-stories in `docs/CONVENTIONS.md`)

**`docs/observability.md` is the map**: what the three signals are for, how a
span physically reaches a collector (there are TWO filters, and only the first
is visible from this repo), how to instrument a model call, and how to read any
of it back. Read it before adding an instrument. The catalogue of instances is
`design/dreamer-agent-loop/02-instrument-blindness.md`; these are the rules it
earned. They are the `measure` verb pointed at our own instruments.

- **One root span per entry point, and stages are its CHILDREN.** Sibling spans
  with no root are not one trace with gaps — they are N traces.
- **Langfuse gets the traces with a GENERATION in them; everything else is
  Prometheus'.** A root declares `nora.trace_kind`; plumbing is dropped at the
  sampler and its outcome ships as a counter label instead. The rule is about
  ROOTS — a stage span with no model call is exactly what makes an early exit
  legible under a root that has one.
- **Spawned work does not inherit the caller's span.** `create_task` copies the
  context of the task that CALLS it, so work handed to a worker starts its own
  trace unless the intended context is captured and attached explicitly.
- **One spelling for "this span is a model call".** `nora.obs.tracing.
  generation` / `annotate_generation` own the `gen_ai.*` and
  `langfuse.observation.*` vocabulary; a call site never writes those names. The
  spelling was hand-rolled twice, diverged, and left every raw call priced at
  zero tokens.
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
- **A span with only a name is worse than no span** — carry what makes the step
  re-derivable. Empty traces read as progress; an absence at least asks.
- **Instruments measure the RESULT; something must measure the COST.** An index
  is used, a call is bounded, a read is scoped: check the PLAN or the bound.
  Never the answer — a correct answer is compatible with any cost.
- **R-27's three states on every instrument: `ok` / `nothing_found` /
  `unavailable`.** A wiring gap is `unavailable` — never `nothing_found`, never
  `ok`, and never two of them on one event.
- **A declared metric with no call site is a lie** — it renders as a series that
  is genuinely always zero. Ship the name with its increment, or not yet.
- **And an increment with no declaration is worse** — `Metrics.inc` raises
  `KeyError`, which the caller's own `except Exception` then reports as the
  failure of whatever it wrapped. `nora_peer_card_empty_total` spent a
  deployment turning every genuinely empty card into a fake transport error.
  `tests/architecture/test_every_recorded_metric_is_declared.py` is the gate;
  a call site whose name it cannot read statically must be PINNED there, never
  skipped.
- **Label vocabularies are DERIVED from the code, never hand-copied.** One named
  constant the code increments against and the help text reads from: `get_args`
  off a `Literal`, plus a RUNTIME check wherever `make gate`'s mypy does not
  reach (issue #61) — a static-only guard in an unchecked package is a claim.
- **Pair every zero-assertion with a positive control.** `== 0` is also true of
  a stage that crashed; assert something only a run that RAN produces.

## The five operational constraints (one line each — full text in `docs/CONVENTIONS.md`)

1. Container builds require `docker build --network=host` (BuildKit's bridge can't
   resolve `deb.debian.org` on this host).
2. Honcho health uses **`GET /health`**, not `GET /v3/workspaces` (POST-only → 405).
3. **signal-cli is Nora's transport, in her compose project.**
   **`--receive-mode=on-start` is load-bearing — never drop it** (without it the
   websocket to Signal never opens and every message is silently dropped). Needs
   **Java 25**.
4. The legacy Hermes gateway is archived at
   `/home/norty/backups/nora-legacy-profile-20260804/`; don't use the old
   `~/.hermes/profiles/nora/` path.
5. Dependencies via **`uv add`**, never raw pip.

## Conventions (short — full text in `docs/CONVENTIONS.md`)

- **One stage, one module, one public entry point** — about RESPONSIBILITIES, not
  lines (a 400-line module doing one thing is fine; count what it is responsible
  for).
- Test-seam policy: production classes take collaborators through `__init__` only
  — no `*_fn` hooks, no `_for_test` params, no module-level mutable singletons.
- A port never gains a second implementation — including a test fake — without a
  shared contract suite both pass (`tests/contract/`).
- **A contract suite scopes its database to the MODULE, not the test.** The
  shared `pg_url` fixture is function-scoped: `CREATE DATABASE` + the full
  migration chain + `DROP`, every test. At ~30 database-backed tests that
  degrades the container enough to fail an unrelated wall-clock test in another
  directory — the cost lands nowhere near its cause. Build once per file and
  `TRUNCATE` between tests; the isolation these suites need is "no rows from
  the last test", not "no schema from the last test".
- A new behavioural rule gets an entry in `design/09-decision-index.md` and a
  named test in `tests/test_acceptance_rules.py::RULE_TESTS`.
- **A test directory names what makes the test TRUE, not what it is about**
  (`tests/README.md`): `unit/` no I/O, `contract/` one suite N implementations,
  `integration/` the wired pipeline, `architecture/` properties of the source.
  The `tests/` root is legacy and takes no NEW files.
- **A test gets its config from the template** — the `example_config_path` /
  `example_config` fixtures — never a path to the repo's `config.toml`. Only a
  test whose SUBJECT is the deployment reads the real file, pinned in
  `tests/architecture/test_config_comes_from_the_template.py`.
- **A fake standing in for a port lives in `nora/testing/`** and is
  parametrized into that port's contract suite. Inline classes are local
  scaffolding only; `tests/architecture/test_port_fakes_live_in_nora_testing.py`
  is the gate and its `_ALLOWED` list only shrinks. Issue #113 for all three.
