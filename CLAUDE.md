# CLAUDE.md

Guidance for Claude Code (and any other agent) working in this repo.

## Reading order

1. `README.md` — what Nora is, how to run her.
2. `ARCHITECTURE.md` — the map: topology, package table, ports, a walk of one
   ambient message and one tag.
3. `design/09-decision-index.md` — one line per decision, pointing at the
   document that made it and the test that enforces it.
4. `design/*.md` — the topology documents the decision index points into.
5. `docs/superpowers/specs/` — history. Dated specs; mark superseded ones
   inside the file itself. Read for *why*, not for current state — that's
   what 1–3 are for.

## The three config files

Three files, three lifetimes, deliberately not merged (DEC-8's argument,
extended):

| file | answers | changes when |
|---|---|---|
| `config.toml` | ports, models, timeouts | the deployment changes |
| `identity.toml` | **who people are** | a human joins |
| `policy.toml` | **who may talk to her, and where** | trust changes |

The last two are gitignored, bind-mounted read-only into the container, and
**loaded once at startup** — editing either without a restart changes nothing
and warns you about nothing. `nora_unmapped_senders_total` is the tell.

`identity.toml` takes one `[[persons.<id>.<platform>]]` block **per account**;
one person may hold several on a platform and they all resolve to a single
`person_id`, which is their Honcho peer (A2, DEC-14). `policy.toml` is
deny-by-default and Nora **refuses to start** if it is missing or allows
nothing — running mute would be indistinguishable from working (R-5).

## Gate commands

Run before claiming anything is done, and before every commit:

```bash
uv run pytest -q                                        # tests green
uv run ruff check nora/ evals/ tests/                    # lint clean
uv run mypy nora/domain nora/config.py nora/app nora/infra   # types clean (strict, scoped)
uv run lint-imports                                      # import boundaries clean
```

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

The module-size gate (spec §10's 300-line hard cap on production modules,
below) is `tests/architecture/test_module_size.py`; it runs inside
`uv run pytest -q` too, no separate command.

## Never

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
  it no longer exists; see below.
- **Never assert a per-tag object was cleaned up by checking that an
  attribute was deleted.** `Handler._prefills`/`_guards` (the leak the
  pipelines phase exists to remove) existed precisely because tests asserted
  on retained *constructed* objects, not counted live ones — an
  attribute-absence check would have passed anyway.
  `tests/architecture/test_no_retained_per_tag_state.py` asserts on
  `gc`-tracked live `ReplyGuard` counts across two different batch sizes
  instead, so the same leak shape can't reappear under a new attribute name
  and still pass.
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

- Module size: soft cap 200 lines, hard cap 300. One stage, one module, one
  public entry point. The hard cap is enforced, for production modules under
  `nora/` only, by `tests/architecture/test_module_size.py`.
- Test-seam policy: production classes take collaborators through `__init__`
  only — no `*_fn` hooks, no `_for_test` params, no module-level mutable
  singletons.
- A port never gains a second implementation — including a test fake —
  without a shared contract suite both pass (`tests/contract/`).
- A new behavioural rule gets an entry in `design/09-decision-index.md` and a
  named test registered in `tests/test_acceptance_rules.py::RULE_TESTS`.
