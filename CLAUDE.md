# CLAUDE.md

Guidance for Claude Code (and any other agent) working in this repo. This file is
the terse, scannable rule set. The reasoning, the war-stories, and the specific
values behind these rules live in **`docs/CONVENTIONS.md`** — read it when a rule
needs its "why"; every terse rule below is only safe because that detail is
preserved.

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

## Reading order

1. `README.md` — what Nora is, how to run her.
2. `docs/CONVENTIONS.md` — the detail behind this file: gate reasoning, the "Never"
   war-stories, the five operational constraints in full, the conventions.
3. `ARCHITECTURE.md` — the map: topology, package table, ports, a walk of one
   ambient message and one tag.
4. `design/09-decision-index.md` — one line per decision, pointing at the
   document that made it and the test that enforces it.
5. `design/*.md` — the topology documents the decision index points into.
6. `docs/superpowers/specs/` — history. Dated specs; mark superseded ones
   inside the file itself. Read for *why*, not for current state — that's
   what 1–4 are for.

## The three config files

Three files, three lifetimes, deliberately not merged (DEC-8's argument):

| file | answers | changes when |
|---|---|---|
| `config.toml` | ports, models, timeouts | the deployment changes |
| `identity.toml` | **who people are** | a human joins |
| `policy.toml` | **who may talk to her, and where** | trust changes |

The last two are gitignored, bind-mounted read-only, and **loaded once at
startup** — editing either without a restart changes nothing and warns you about
nothing (`nora_unmapped_senders_total` is the tell). `identity.toml` takes one
`[[persons.<id>.<platform>]]` block **per account**, all resolving to one
`person_id` (their Honcho peer, A2/DEC-14). `policy.toml` is deny-by-default and
Nora **refuses to start** if it is missing or allows nothing (R-5).

## Gate commands

Run before claiming anything is done, and before every commit:

```bash
uv run pytest -q                                         # tests green
uv run ruff check nora/ evals/ tests/                    # lint clean
uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence   # types clean (strict, scoped)
uv run lint-imports                                      # import boundaries clean
```

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
- **Never read an instrument's output as evidence without first checking it can
  measure the thing** — state what a positive result would look like before
  concluding from a null one.
- **Never claim a merge, deploy or fix without reading back the state you claim**
  — `git log -1`, `docker exec … import config`, the metric endpoint. Every time.
- **Never run two agents against this checkout at once** — sequence them, or give
  each a worktree.
- **Never trust `alembic revision --autogenerate` for `idx_messages_conv_ts`** —
  it is excluded on purpose; a revision touching it is hand-written.

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
- A new behavioural rule gets an entry in `design/09-decision-index.md` and a
  named test in `tests/test_acceptance_rules.py::RULE_TESTS`.
