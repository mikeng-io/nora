# Git hooks

Installed with `make hooks` (sets `git config core.hooksPath .githooks` — a
repo-level setting shared by every worktree, so one run per clone covers all of
them). Two things worth knowing before you trust it:

- **The hooks fire only where `.githooks/` exists on disk** — i.e. on a
  checkout of a branch that carries this commit. On the main checkout, or on a
  worktree of an older branch, git silently skips the missing path and the
  commit goes through unchecked; `pre-push` is the backstop for exactly that
  gap (it runs wherever the push happens from, on whatever tree is checked out
  there).
- **The checker resolves against the worktree you are committing in**
  (`git rev-parse --show-toplevel`), never `--git-common-dir` — in a linked
  worktree the common dir is the main checkout, which may hold a different
  (older) copy of the rules. Verified 2026-09-14: with the common-dir path,
  `git commit -m 'chore: x'` in a linked worktree failed with "can't open file
  …/.git/../scripts/check_commit_subject.py".
- **`core.hooksPath` must be the RELATIVE `.githooks`.** An absolute path
  (`/path/to/main-checkout/.githooks`) runs the MAIN checkout's hooks in every
  worktree, so a branch's new or changed hook never fires until the main
  checkout has it. `git config core.hooksPath` should print `.githooks`;
  `make hooks` sets it.

- **`commit-msg`** — runs `scripts/check_commit_subject.py` on every commit.
  The closed type list and the authored-merge rule are enforced here so the
  convention stops depending on memory. `--no-verify` bypasses it; if you use
  that, name the reason in the commit body. Then scans the message and the
  author/committer identity for identifiers (below).
- **`pre-commit`** — scans the STAGED blobs for identifiers (below).
- **`pre-push`** — same subject checker across EVERY commit in the pushed
  range, not just the head: the commits that need it most are the ones made
  before the hook was installed. Capped at 200 per ref, and it says so when
  the cap binds. Then the identifier scan over the same range.

The rule these enforce lives in `CONTRIBUTING.md` §Commit messages / §Merge
strategy. The checker is the typing aid; the doc is the contract. To change
either, change both in one commit.

## Identifiers (#283)

`scripts/pii_scan.py`, stdlib `python3`. It judges what a commit ADDS — the
staged blobs against HEAD (and MERGE_HEAD in a merge), the message and the
author/committer identity, and at push time every blob, message and identity
the push introduces (`--not --remotes`, up to `NORA_PII_PUSH_MAX`=1000 commits;
past that it is an ERROR, not a partial look). The push scan is the backstop for a
commit made with `--no-verify`, before the hooks were installed, or whose
payload a later commit `git rm`-ed: the object is still in the push.

Exit 0 PASS, 1 FAIL, 2 ERROR ("could not look"); both non-zero block. The
output names path, line and rule, and masks the value.

**Setup, once per machine.** The real values live in a denylist OUTSIDE the
repo, `~/.config/nora/pii-denylist.txt` (or `$NORA_PII_DENYLIST`), built from
the deployment's `identity.toml` and the scrub map:

```bash
python3 scripts/pii_denylist_build.py --identity <identity.toml> --map <scrub map>
```

It prints counts, never values, and writes mode 600. Without it every commit
is an ERROR. A machine that genuinely has no access to the real values (a
fresh CI runner) sets `NORA_PII_DENYLIST_ABSENT=<reason>`; the reason is
printed on every run and only the shape rules apply.

**What it checks**, in order:

1. The denylist, after normalisation (case and separators, full-width
   digits, zero-width characters, dashless and upper-case UUIDs, JID and
   bare-local phone forms, url-safe and percent-encoded base64). A hit is a
   known real value: remove it. It cannot be acknowledged, and no model ever
   sees the line.
2. Shapes the denylist does not know: phone numbers in any country, emails,
   platform snowflakes, UUIDs, WhatsApp JIDs, Signal group ids, Tailscale
   names and addresses, GPS in photo/video metadata, and coordinate tables.
   Coordinates are read as number FIELDS (decimals, comma decimals, split
   int/fraction columns, DMS, Plus Codes, and integer microdegrees in a file
   that names coordinates) and scored on dwell: three fixes within ~220 m of
   each other, or dwell rows x dwell places over 11, is a table. Obvious
   placeholders pass: `example.com`, `555-01xx`, runs of one digit, all-zero
   UUIDs, every value in `identity.example.toml`.
3. Fail closed: UTF-16, undecodable bytes, a symlink, a submodule and a blob
   over 8 MiB are ERRORs, never skips (#166).

**Only what the change adds.** A value on an added line counts when it occurs
there more often than on the lines the change removed, against every parent.
The residue already in the tree (the scrub track's inventory, #167) does not
block an unrelated edit; adding one more occurrence does.

**Declarations** live in `.githooks/pii-allowlist.txt`, each with a reason:
`path-exempt`, `binary` (scanned for embedded strings instead of refused),
`symlink`, `gitlink`, `large`, `coords <path> sha256=<digest>` (a location
table pinned to its content: `python3 scripts/pii_scan.py declare-coords
<path>` prints the line), `value` and `value-re` (placeholders). They are
read from the PARENT commit: commit the declaration first, then the change it
covers. A declaration in the same commit as its payload exempts nothing
(#283 criterion 3).

**Acknowledging a finding.** For a shape or judge finding you have looked at
and judged harmless, re-run the commit with its id:

```bash
NORA_PII_ACK=1a2b3c4d5e6f,0f9e8d7c6b5a git commit ...
```

The `commit-msg` hook writes a `PII-Ack: <id>` trailer for each, so the
decision is in the history, and pre-push honours exactly those trailers. The
id hashes the path and the line: it acknowledges that line and nothing else.

**Optional model layers**, off by default (`scripts/pii_judge.py`):

| env | what it does |
|---|---|
| `NORA_PII_JUDGE=laya` | scores every added line and commit-message line with Laya on Apple Silicon (`laya-mlx` in the interpreter `NORA_PII_LAYA_PYTHON`; checkpoint `NORA_PII_LAYA_MODEL`, default the English one). Local; a line at or above 0.3 is a finding to acknowledge or fix. |
| `NORA_PII_JUDGE=jev` | the same through TypeSafe's hosted `jev-1.13.0`. **Every judged line leaves the machine**: not for real repo content until TypeSafe's zero data retention is confirmed. |
| `NORA_PII_LLM=<ollama tag>` | a local ollama model re-reads each flagged line with its context and answers real/fake/unsure, printed beside the finding. It clears a finding only with `NORA_PII_LLM_CLEARS=1`. |
| `NORA_PII_JUDGE_THRESHOLD` | overrides the judge's threshold. |

An unreachable or failing model is an ERROR, never a PASS. What a model can
decide is whether a line LOOKS like personal data copied from real records;
it cannot know whether a name belongs to a real person. The denylist is the
ground truth for identity. Calibration: `scripts/pii_judge_eval.py` (a
generated 55-line set; nothing in it is real).

**What it does not catch.** A headerless integer-microdegree table in a file
that never names coordinates, geohashes, a constant coordinate offset, a
number split across string concatenation, and anything an author disguises
on purpose. It stops a corpus pasted as it came out of a database — how the
one real incident happened — and it is not what decides a public release
(#167 does).
