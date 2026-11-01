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

`make test` is incremental; `make test-fast` and `make test-affected` are aliases.
They compare staged, unstaged and untracked changes against HEAD, or the last
commit (HEAD~1) when clean. Only exact generated untracked LFS post-hooks are
ignored. `BASE=<ref>` overrides auto selection. Unmapped/shared-helper changes
and selector errors run the full suite; the command reports its base and scope.
`make test-all` explicitly runs the full serial suite. For a named file/function,
use `uv run python -m scripts.pytest_gate <file-or-node>`.

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
- `record_pg_url` opts media-fetch and tool-registry tests into a module clone.
  Each requesting test resets messages (with dependent tables), memory nodes,
  edges, projection retries and row history, including their owned sequences.
  History capture is disabled only inside the reset transaction. Migration seeds
  remain intact. This fixture is only for record-only consumers without schema
  changes, extraction or graph-projection context; engines must be disposed.
- Schema parity uses a module clone for catalogue reads and CHECK probes whose
  SAVEPOINT always rolls back. Deliberate drift still uses scratch databases.
- Make targets use `python -m scripts.pytest_gate`: up to two file-grouped
  workers, followed by a serial phase for the three timing modules declared in
  that runner. Explicit files/node selectors keep their selection. `TEST_WORKERS`
  defaults to two; use zero or one to reduce concurrency. Full-gate stays serial.
- The runner and direct pytest collecting under `tests/` share one per-user host
  lease across updated worktrees. Other component/ad-hoc suites must use the runner
  or explicitly load the plugin. A competing job is refused before workers start.
  Workers retain
  shares after controller exit; generation checks refuse late stale workers.
  The runner retains its share across both phases. Direct parallel pytest must
  exclude timing modules; use the runner to select and execute them correctly.
  The lock bounds concurrent jobs, not total writes. Older checkouts without the
  plugin remain stopped until reconciled; an advisory lock cannot govern them.
- Collection-only reads do not acquire the execution lease or start xdist workers;
  the rule-to-test catalogue check can collect during an admitted test job.
- Servers remain lazy, disk-backed, owned by each requesting worker/session,
  with Testcontainers/Ryuk cleanup. No cross-run state, shared server, tmpfs,
  altered durability settings or FILE_COPY default is introduced. Pure tests
  need no Docker. A killed worker still relies on the existing Ryuk lifecycle.

Local gate receipts:

- `make gate` uses the canonical runner: code/mixed changes run static checks plus
  affected tests; ordinary docs run their reader tests. Canonical in-repository
  `.claude/skills/<name>` links use the linked SKILL.md readers plus the agent-skill
  contract, through the same classification in local gates and hosted CI.
  Missing/redirected links and runtime changes stay broad. Unknown paths/selection
  errors remain broad. README guides under `tests/` are docs; other test/fixture Markdown
  and non-collectible Python helpers remain broad. Only byte-exact regular Git LFS
  post-hooks are excluded from untracked noise; edits, symlinks and tracked hooks
  still count. No arbitrary executable hook is ignored.
- A local successful receipt lives in this worktree's Git directory, outside the
  source tree, with its command, completion time, rc and candidate/context key.
  Only an identical clean HEAD/tree/base, worktree, environment, installed Python
  distributions/files, tools/Git configuration, private input hashes and Docker
  daemon/images/container configuration/state can reuse it. Context must remain
  unchanged during the successful run. Secrets are hashed in memory, never stored
  in the receipt or printed. This is cooperative source acceptance as of the
  recorded run, not a tamper-proof attestation or fresh external-service health.
- Environment directories, PATH entries (including relative/empty entries), and
  the explicit or default tiktoken cache use a private recursive content manifest.
  The total is bounded to 4,096 entries, 64 MiB of file contents and a checked
  two-second traversal/read budget. Enumeration and sizing precede content reads.
  Symlinks, special files, unreadable/changing inputs and exceeded limits reject
  reuse. Filesystem calls can block beyond the checked time budget. Large HOME,
  working or temp directories and ordinary tool paths may therefore always MISS;
  no standard environment directory is silently exempted. Use `gate-reuse` to
  inspect eligibility without accidentally starting another cold gate.
- Dirty trees, unknown untracked files, hidden index flags, external editable
  dependencies, PYTHONPATH overrides, Make command overrides, unavailable context
  and CI do not reuse receipts. Go bridge changes also remain fresh; Go cache
  dependency internals are outside this first Python-acceptance cache.
- `make gate-reuse` checks for a matching receipt only; a miss exits nonzero and
  runs no checks. Use it for a bounded warm readback without accidental cold I/O.
- `make gate-fresh` bypasses reuse. `make full-gate` is always fresh and serial.
  Either invalidates the prior receipt before execution, as does any actual gate
  attempt; failures/interruption never leave the old green receipt reusable.
  Host admission applies before cache lookup and remains held through publication.
- A `REUSED` line means the earlier matched receipt was used, not new passing tests
  or new performance measurements. PR/issue/report updates do not rerun validation.
