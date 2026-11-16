# Managed-to-file export

`scripts/managed_identity_export.py` reads the CURRENT managed identity and
access authority — Nora SQL bindings, Kratos traits, Keto permissions — and
produces a fresh private `identity.toml`/`policy.toml` pair plus an
`export-manifest.json`, validated through the real file loaders and
compared verdict-for-verdict against live managed checks.

This is the preparation half of the managed-to-file handoff
(`design/managed-identity-access/identity.md` «Backend selection and
lifecycle»). It is **not** the authority switch: adopting the bundle is the
separate operator-approved step in `docs/runbook.md` «Managed identity
activation and recovery», which additionally requires stopping writers,
backup, session invalidation and read-back. The command never changes
backend selection, configuration, source files, secrets or running
sessions, and nothing here is reachable from HTTP, a model or a room.

## The two commands

```bash
# plan — full validation, prints the would-be digest and counts, writes nothing
uv run scripts/managed_identity_export.py plan \
    --policy /etc/nora/policy.toml \
    --database-url "$DATABASE_URL" \
    --kratos-admin-url http://127.0.0.1:4434 \
    --keto-read-url http://127.0.0.1:4466

# export — the same validation, then publish to a FRESH directory
uv run scripts/managed_identity_export.py export \
    --policy /etc/nora/policy.toml \
    --output /secure/path/export-2026-01 \
    --database-url "$DATABASE_URL" \
    --kratos-admin-url http://127.0.0.1:4434 \
    --keto-read-url http://127.0.0.1:4466
```

- `--policy` is the deployment's CURRENT policy file — its room
  declarations, unit bindings, labels and flags. It is a topology input,
  not grant authority: the emitted `persons` lists are re-derived from
  live managed verdicts, and `[dm]` admission lists or guild rooms
  refuse. The file is read ONCE — one immutable snapshot wires the
  managed services and drives room derivation, verdict comparison,
  document building and the emitted `policy.toml` text; before a bundle
  may appear the source is read back once more and a file that changed
  mid-run refuses (`untrustworthy:source-changed`).
- Kratos/Keto keys come from `--kratos-admin-key-env`/`--keto-key-env`
  naming the environment variable, same as the admission command.
- The output directory must be ABSENT — an existing file, link or
  directory (even an empty one) is refused, never written through. The
  bundle lands in one `mkdir`: the directory is mode `0700` and every
  file mode `0600` from creation, whatever the operator's umask, and the
  manifest is written last — it is the bundle's completeness mark. A
  failed write removes only the files that invocation staged.

Exit codes: `0` complete · `2` refused (unsupported, ambiguous or
unfaithful state; occupied destination) · `3` unavailable (a store could
not answer — retryable). Print output is bounded counts, named refusal
codes and digests only — never identifiers, keys, person ids, room ids
or account values. The identifying specifics stay inside each refusal's
private detail and the private manifest; exception paths report the
error's TYPE name only.

## What the export does

Under `bindings.mutation()` — the same advisory lock every managed writer
serialises on, so the bundle is one coherent cut — the command:

1. reads the complete inventory from managed SQL: persons (including
   suspended), accounts (including revoked), identifiers, former-person
   aliases, memberships and their support provenance, operation journals,
   identity holds, authority blocks, and link-request state;
2. verifies every person's Kratos record through the correlated
   `kratos_identity_id` — a read-only `GET /admin/identities/{id}`, never
   an ensure/create — checking the returned record's id, `external_id`
   and lifecycle (`active`/`inactive` for suspended) against the SQL row,
   then reads descriptive traits (`display_name`, `social`, labels) from
   that verified snapshot. An unreachable Kratos is `unavailable`, a
   missing/mismatched/stale record a refusal — never guessed;
3. reads every relation tuple on each declared scope from Keto;
4. computes the verdict matrix — `access.check` for every room, every
   known person (live ids AND retired aliases, which fold identically)
   and a never-registered probe id, for all five permits — and derives
   each room's emitted `persons` list from it. A `persons = "*"` convo
   always refuses: the file wildcard would admit identifiers managed has
   never registered, and freezing the current roster would close the room
   — neither shape is faithful;
5. builds `identity.toml` (live bindings, archived authored metadata
   overlaid where the archived identifiers verify against CURRENT live
   ownership — never identifier-set equality, never restored ownership —
   `former_person_ids` for aliases, `social = false` for opted-out and
   suspended people) and `policy.toml` (the snapshot's text verbatim,
   only `persons` re-derived). Suspended people's accounts are never
   emitted: they stay private provenance in the manifest, identifiers and
   grouping intact, alongside the actual current Kratos labels the file
   cannot carry;
6. loads the generated pair through the REAL `load_persons`/`load_policy`,
   then compares the file predicate against managed: every matrix cell
   must agree, open admission must agree, every emitted identifier must
   resolve to the same person in both backends, and no suspended or
   revoked identifier may resolve at all;
7. only then hands back the bundle for publication.

## Refusals — the whole export refuses when

- `unsupported-input:guild:*` / `unsupported-input:dm-admission:*` — the
  topology input carries shapes the export cannot evaluate;
- `untrustworthy:*` — malformed rows or traits, dangling accounts,
  missing Kratos correlation, state disagreement, a malformed archived
  journal;
- `ambiguous-ownership:*` / `conflicting-ownership:*` — a shared Kratos
  identity, a former alias that is still a live row, one identifier value
  claimed by two accounts (managed's own `resolve` cannot answer that
  deterministically either), or an archived binding that now belongs to a
  different person;
- `incomplete-publication:*` — unfinished operation journals, identity
  holds, authority blocks, or a link request that has not reached a
  terminal state;
- `unrepresentable:*` — a person id outside the file id alphabet, an
  identifier kind the file format has no field for, two same-platform
  accounts without distinct labels;
- `unfaithful:*` — management authority or an unknown relation on a
  declared scope, speak/view divergence, an open room (`*`) in any shape
  (it denies a known person, or is simply unrepresentable dynamically),
  `also_ids` forms disagreeing, an account with no identifier — anything
  file mode cannot express exactly;
- `untrustworthy:source-changed` — the topology file changed between the
  snapshot and the pre-publication read-back;
- `generated-invalid-*` — the generated pair failed the real loaders
  (self-check failure);
- `unfaithful:verdict-mismatch` / `resolve-mismatch` /
  `open-admission-mismatch` / `social-mismatch` —
  the generated file disagrees with managed on the compared predicate —
  no bundle is published.

`unavailable` instead of a refusal means a store could not answer (Kratos
unreachable, Keto read failed, database down): fix and retry — the gate
keeps the read coherent.

## What folds, what drops, what is never exported

- **Folds into `persons` lists** (through the verdict matrix): room
  memberships, blanket restrictions, and grant_/deny_ of `speak`/`view`.
  `deny_` of a management permit folds too — denial is the file default.
- **Refuses, never drops**: `admin` and `grant_` of `manage_members`,
  `manage_grants` or `delegate` — delegated administration has no file
  surface (the file backend's administrator IS the operator), so its
  presence names the limit rather than silently dropping who may
  administer.
- **Drops (recorded in the manifest)**: `former_person_ids` pointing at
  persons no longer present, archived workspace bindings the topology no
  longer declares, and non-portable per-account labels on traits.
- **Never exported**: Kratos credentials, bearer values, session proofs,
  account ids, operation internals. Provenance (digests, the archive
  operation id, suspended/revoked records, folded relations, membership
  supports) lives privately in `export-manifest.json` beside the bundle.

## After export — adopting the bundle

The bundle is inert until an operator adopts it. Per the runbook's
«Managed identity activation and recovery»: stop Nora's writers, back up
the managed database and the current files, place the bundle, switch the
configuration back to file mode, invalidate the old Ory sessions, start
file mode, and read back a known grant and a known denial before
considering the switch done. None of that is this command's job.
