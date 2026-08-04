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
- **Never touch the signal-cli daemon.** It is neutral and shared, not owned
  by Nora — see below.
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
3. **The signal-cli daemon is neutral and shared — never touched.** It is not
   owned by Nora. `derive_account` reads from it; nothing writes to it.
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
