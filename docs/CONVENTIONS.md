# Conventions & operational detail

The long-form behind `CLAUDE.md`'s rules. `CLAUDE.md` carries the terse rules an
agent must not violate; this file carries the *reasoning*, the war-stories, and
the specific values. Read it when a rule needs its "why" — every mistake recorded
here cost an afternoon, and the terse rule is only safe because the story is
preserved. Nothing here is optional context; it is the load-bearing detail moved
out of `CLAUDE.md` so that file can stay scannable.

## Gate commands — the detail

The schema gate is `tests/infra/test_schema_parity.py`, not `alembic check`.
`alembic check` guards column presence, column type and server defaults; it is
provably blind to the CHECK constraint and to the `ts DESC` index ordering, and
`idx_messages_conv_ts` is excluded from autogenerate because the expression
index otherwise reports drift on every run. Both run in `uv run pytest -q`.
See DEC-11 in the decision index for the full story, including a 2026-08-05
correction to its original server-default claim.

`uv run lint-imports` uses a local cache (`.import_linter_cache/`, gitignored).
If it reports a phantom contract failure that doesn't reproduce after a clean
checkout, delete that directory before spending time on it.

There is no longer a module-size gate. Spec §10's 300-line hard cap was
deleted 2026-08-06 — see the Conventions section below for why, and prefer
counting a module's responsibilities to counting its lines.

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
- **Never point signal-cli at a different account, re-register it, or unlink
  its device** without an explicit decision — that store holds the device
  registration, and losing it means re-linking. Nora *does* now own the daemon
  (see constraint 3); routine reads and RPC calls are ordinary, destructive
  account operations are not.
- **Never reference the archived legacy path** (`~/.hermes/profiles/nora/`) —
  it no longer exists; see constraint 4.
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
- **Never run two agents against this checkout at once.** Three times on
  2026-08-06 a subagent's `git add -A` swept another session's staged work into
  its commit, and once an agent amended a commit it did not author. Nothing was
  lost, but commit history stopped describing who changed what. Sequence them,
  or give each a worktree.
- **Never run `alembic revision --autogenerate` and trust the result for
  `idx_messages_conv_ts`.** It is excluded from autogenerate on purpose; a
  revision that touches it must be hand-written.

## The five operational constraints (spec §2, verbatim)

These came from `design/08-stack-refactor-brief.md` and are preserved here
because the spec that retired that document said they were the most
operationally valuable text in it. Losing any of these wastes an afternoon.

1. Container builds require `docker build --network=host` — BuildKit's default
   bridge cannot resolve `deb.debian.org` on this host (systemd-resolved stub
   `127.0.0.53`).
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
   `/api/v1/rpc` asks the primary to re-send it — that is what made the
   `Ridgeline` group appear after `listGroups` had returned `[]`.
4. The legacy Hermes gateway is archived at
   `/home/norty/backups/nora-legacy-profile-20260804/`. Do not reference the
   old `~/.hermes/profiles/nora/` path.
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
- Test-seam policy: production classes take collaborators through `__init__`
  only — no `*_fn` hooks, no `_for_test` params, no module-level mutable
  singletons.
- A port never gains a second implementation — including a test fake —
  without a shared contract suite both pass (`tests/contract/`).
- A new behavioural rule gets an entry in `design/09-decision-index.md` and a
  named test registered in `tests/test_acceptance_rules.py::RULE_TESTS`.
