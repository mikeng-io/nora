# tests/

What each directory means, so a new test has a home. The reasoning, the
measurements and the war-stories are in `docs/maintainers/conventions-rationale.md` under **Test
structure**; this is the map.

A directory names **what makes the test true**, not what the test is about.

| directory | a test here is true because … | needs |
|---|---|---|
| `unit/` | the logic is right; every collaborator is a fake or a value | nothing |
| `contract/` | ONE suite passes against N implementations of a port — the fake and production | Postgres for the SQL ones (module-scoped, truncated between tests) |
| `integration/` | the wired pipeline behaves, through the real composition root | Postgres, real HTTP servers |
| `infra/` | a real adapter agrees with its real backend | Postgres, rustfs, docker |
| `architecture/` | a property of the SOURCE holds — read off the AST, never the runtime | nothing |
| `web/` | the aiohttp control-plane surface answers | an aiohttp test server |
| `domain/` | *legacy, subject-named* — put new pure-logic tests in `unit/` | nothing |
| `.` (this root) | **legacy** — holds no NEW test files; the files here move under R-A (#113) | — |

Three rules, each with a gate under `architecture/`:

- **Where a test lives** (R-A): the table above. The move of the root's files is
  deferred; the container-fixture gate lands with it.
- **Where a test gets its config** (R-B): the `example_config_path` /
  `example_config` fixtures in `conftest.py`, never a path to the repo's own
  `config.toml` or `config.example.toml`. Only a test whose SUBJECT is the
  deployment reads the real file, and it is pinned in
  `architecture/test_config_comes_from_the_template.py`.
- **Where a fake lives** (R-C): a class standing in for a `nora/domain/ports.py`
  port lives in `nora/testing/` and is parametrized into that port's suite under
  `contract/`. Inline classes are local scaffolding — a fault injector, a
  subclass of a shared fake with one twist. Anything else is pinned with a
  reason in `architecture/test_port_fakes_live_in_nora_testing.py`, and the
  list only shrinks.

Shared modules at this level:

- `conftest.py` — the fixtures every directory shares: the Postgres
  testcontainer (`pg_url`, `pg_admin`, `pg_template`), rustfs, the doctrine
  store, the exported-spans provider, the two config fixtures.
- `builders.py` — the ONLY module that constructs or copies a `nora.config`
  object (`override_config`, `make_*`, `copy_example_config`).
- `fakes.py` — doubles that are NOT port implementations (`SinkSender`,
  `FakeClock`, `FixedModelSource`). Port fakes live in `nora/testing/`.
- `fixtures/` — captured data files.

Run `make test-affected` (or the tests you touched) while iterating, and
`make gate` once, on the final head, before claiming a PR is done — never a
gate per fix round. `make full-gate` runs the whole suite serially and
unconditionally (`AGENTS.md`, *Gate commands*).

Disposable PostgreSQL reuse:

- `pg_url` remains a fresh migrated database per test, with `DATABASE_URL`
  exported. Integration restarts and migration/destructive tests keep it.
- `pg_module_url` opts into one migrated clone per module **per worker**.
  It does not export `DATABASE_URL`. The caller must reset its own tables and
  dispose every engine; it is not a universal rollback or flush fixture.
  Culture, entries and memory-graph contracts retain their existing TRUNCATE
  resets (including the history-capture settings). Migration seeds, sequences,
  history outside those tables and non-row state are not globally reset.
- Schema parity uses a module clone for catalogue reads and CHECK probes whose
  SAVEPOINT always rolls back. Deliberate drift still uses scratch databases.
- Parallel Make targets use `--dist loadfile` to avoid rebuilding module
  fixtures on several workers. `TEST_WORKERS` defaults to eight; override with
  `make test-affected TEST_WORKERS=2` when sharing a host. File grouping can leave
  a long tail; validate the final full gate before claiming a suite speedup.
- Servers remain lazy, disk-backed, owned by each requesting worker/session,
  with Testcontainers/Ryuk cleanup. No cross-run state, shared server, tmpfs,
  altered durability settings or FILE_COPY default is introduced. Pure tests
  need no Docker. A killed worker still relies on the existing Ryuk lifecycle.
