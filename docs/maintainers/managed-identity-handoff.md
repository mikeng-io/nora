# Managed identity handoff — admitting `identity.toml`/`policy.toml`

`scripts/managed_identity_handoff.py` is the installation-only initial
import described by `design/managed-identity-access/identity.md` «Backend
selection and lifecycle»: when the managed backend is *selected*, the
operator admits one validated mapping of the existing people and grants.
This command performs that admission into an **empty managed target**. It
does not select the backend, edit configuration, or activate anything —
`backend = "file"` keeps working exactly as before, with or without this
command having run.

It is reachable only as a local command — never through HTTP, a model, or
a room. Private account mappings, the source files, and credentials stay
where they already live; the command prints counts and digests only.

## What it does

`plan` runs the real loaders (`load_persons`, `load_policy`) and computes
the exact intended effect: every person (stable `person_id`s, including
policy-listed people with no account yet), every account with all its
typed identifiers and its non-matchable `label`, former-person aliases,
descriptive names, explicit social opt-outs, and the effective room
`member` grants at their declared scopes. Both files' SHA-256 digests and
the effect digest bind a durable import identity
(`file-admission:<digest>`); the same bytes always name the same import.
The loaders parse private temporary copies of those exact bytes. The import's
private journal retains the original source text, including authored metadata
and workspace bindings, for faithful export after the source paths change.

`apply` then, under the shared mutation gate (`bindings.mutation()`):

1. **Claims the target** — a direct read of every managed table, not the
   active-only `persons()` view. Anything managed already present —
   suspended people, revoked accounts, aliases, memberships, holds,
   authority blocks, link rows, or any foreign journal — refuses the
   claim, as does an existing Kratos identity already correlated to an
   imported person.
2. **Journals the effect** before any write (`file_admission` operation
   row), then runs journaled steps: people → identity holds → Kratos
   identities + correlation → aliases → memberships → containment +
   `member` relations → read-back. Each person commits WITH this
   import's identity hold in one transaction — never readable
   unprotected — and holds keep every imported person `unavailable` to
   dependent managed access until the read-back proves bindings,
   Kratos, memberships, Keto facts, and the full person×room verdict
   matrix agree with the file predicate. The `complete` journal outcome
   and the release of all and only this import's holds then commit
   together: an outside reader sees complete-plus-unheld or keeps
   incomplete-plus-held, never one without the other.
3. **Reports** `complete` only after that read-back. A crash or outage
   leaves an `incomplete` journal and re-running the *same* command with
   the *same* files resumes the exact journaled steps — no new people or
   accounts, no duplicated grants. Changed files derive a different
   import identity and are refused (`different-import`), so a successful
   import is never overwritten.

One invocation reads the files **once**: the printed plan, the target
policy wiring and the applied import all bind that single captured
effect — a file changed during setup cannot swap the effect behind the
plan it reported. A durable journal row under the same import identity
is bound by operation kind and the full effect snapshot; a mismatch
refuses as a conflict (`effect-conflict`), it is never read as the old
import's outcome. An **exact completed retry** returns the recorded
historical receipt as-is: it asserts that this import completed, never
that current state still equals the original file — so it succeeds after
later legitimate management changes and unrelated journals, writes
nothing, and does not restore initial traits or grants.

## Usage

```
uv run python scripts/managed_identity_handoff.py plan \
    --identity identity.toml --policy policy.toml

uv run python scripts/managed_identity_handoff.py apply \
    --identity identity.toml --policy policy.toml \
    --database-url postgresql+asyncpg://… \
    --kratos-admin-url http://127.0.0.1:4434 \
    --keto-read-url http://127.0.0.1:4466 \
    --keto-write-url http://127.0.0.1:4467
```

`--database-url` also accepts `DATABASE_URL`. Optional:
`--kratos-admin-key-env`, `--keto-key-env`, `--timeout-seconds`.
With all four target flags, `plan` additionally runs the same non-mutating
preflight apply runs — the durable attribution, the empty-or-owned reads
and the Kratos absence/collision check — and prints `target: fresh`,
`target: resume`, the completed import's recorded receipt or the refusal —
without writing.
`fresh` is reported only on an explicit absence answer for every imported
person: a held external_id refuses, and a target that cannot answer is
unavailable, never fresh.

Exit codes: `0` complete/ok · `2` refused · `3` incomplete or unavailable
(retry the same command).

## What is preserved — and what is not yet representable

Preserved exactly: stable `person_id`s, multiple same-platform accounts
per person, all typed identifiers per account, account `label`s,
`former_person_ids` aliases, descriptive names, and the per-person social
opt-out (Kratos trait `social`). Descriptive metadata with no managed
home — `aliases` names, Instagram/Life360 metadata, Honcho workspace
bindings — remains in the journal's private source snapshot for a later export
unit; it is never treated as ownership evidence.

Published: the effective file `speak`/`view` grants as room `member`
relations for explicit person lists, plus
Nora-validated room→unit→workspace containment. Nothing is granted at
unit or workspace level, and no management permit is inferred.

Refused before any write — the whole plan fails, naming the limit:

- a `server`/`guild` conversation (dynamic channel sets have no exact
  scoped representation),
- a `persons = "*"` conversation (enumerating today's people would change
  the dynamic grant for people registered later),
- a non-empty `[dm]` admission list,
- any file the loaders reject (invalid shapes, ambiguous identifier
  ownership), or a changed source on retry,
- a durable journal under the same import id with a different operation
  kind or effect snapshot (`effect-conflict`),
- a target that holds anything the import does not own.

Managed-to-file export and initial administrative delegation are separate
units and are not performed here.
