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

`scripts/pii_scan.py`, stdlib `python3`. It judges what a commit ADDS: the
staged blobs against HEAD (and MERGE_HEAD in a merge) at `pre-commit`; the
WHOLE message file (comment lines and text past the scissors included: `git
commit -m` and `--cleanup=verbatim` keep them) and the author/committer
identity at `commit-msg`; and at `pre-push` every blob, message, identity,
tag message and tagger that the push gives the target remote.

The push scan asks the TARGET remote what it already has (`git ls-remote`)
rather than trusting local remote-tracking refs, which go stale (a branch
deleted on the remote), can be written by anyone (`git update-ref`) and
describe a different remote when you push somewhere else. A remote it cannot
reach is an ERROR. Pushing to a remote that has nothing yet scans the whole
history, up to `NORA_PII_PUSH_MAX`=1000 commits; past that it is an ERROR,
not a partial look. Tags are scanned too: an annotated tag's message and
tagger, and a lightweight tag on a bare blob or tree (every object in it the
remote does not already reach).

Exit 0 PASS, 1 FAIL, 2 ERROR ("could not look": no denylist, a malformed
`NORA_PII_*` setting, an unreadable blob or container, an unreachable remote
or judge, a crash); both non-zero block. The output names path, line and
rule, and masks the value.

**Where the hooks do not run.** `git am`, `git cherry-pick` and `git rebase`
create commits without `pre-commit` (and cherry-pick and rebase without
`commit-msg`), and `git commit --no-verify` skips both. Pre-push is the
backstop for all of those. **`git push --no-verify` skips pre-push as well,
and nothing server-side repeats the check**: there is no CI job and no
server hook. A CI job could run the shape layer and the structural checks on
every pull request (`NORA_PII_DENYLIST_ABSENT=ci python3 scripts/pii_scan.py
commits <range>`; it honours the `PII-Ack:` trailers already in the
commits). It could not run the denylist, which must never be in CI, so it
would miss exactly the known real names, hostnames and ids that are the
commonest finding; it would catch phones, emails, ids, coordinate tables and
media GPS. Not built in this change.

**Git LFS** is not used in this repo (no `filter=lfs` in any
`.gitattributes`). If it ever is: an LFS file reaches the scanner as its
pointer text, not its content, and `core.hooksPath=.githooks` replaces LFS's
own `pre-push` hook, so `git lfs push` would have to be wired in by hand.

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

1. The denylist, in every spelling the matcher knows: case; separators
   between digits (up to four characters, so `+<cc> NNNN - NNNN` is one
   number), including Unicode dashes, the minus sign and no-break spaces;
   full-width digits; zero-width and control characters; Cyrillic and Greek
   look-alike letters; Traditional and Simplified Chinese (folded through
   OpenCC's character table) and spaces inside a CJK name; `Family, Given`
   order and a middle initial; dashless and upper-case UUIDs; JID and
   bare-local phone forms; url-safe and percent-encoded base64. Each line is
   also DECODED and matched again: JSON `\uXXXX` escapes, URL percent-
   encoding, HTML entities, and base64/base64url segments (a JWT's payload),
   two layers deep. A hit is a known real value: remove it. It cannot be
   acknowledged, and neither it nor any line holding a denylisted value (new
   or already in the file) is ever sent to a model. A shape finding whose
   digits sit inside a denylisted number is reported as that number, not as
   an acknowledgeable shape.
2. Shapes the denylist does not know: phone numbers written with a `+` (any
   country), in a `wa.me/`, `tel:` or `sms:` link, as E.164 without the `+`
   for Hong Kong, Macau, Singapore, Taiwan and mainland China mobile ranges,
   or as a bare 8-15 digit run on a line with a phone word (phone, call,
   reach, whatsapp, 電話 ...); emails; platform snowflakes; UUIDs (dashless
   only on a line that says id/user/member/device); WhatsApp JIDs; Signal
   group ids; Tailscale names and addresses; GPS in media metadata (EXIF in
   JPEG/TIFF, a PNG `eXIf` chunk, any ISO 6709 point such as QuickTime's
   `©xyz` or `com.apple.quicktime.location.ISO6709`); and coordinate tables.
   Coordinates are read as number FIELDS (decimals, comma decimals, split
   int/fraction columns, DMS, Plus Codes, and integer microdegrees in a file
   that names coordinates) and fail when three fixes fall within ~220 m of
   each other, when dwell rows x dwell places exceed 7 (two fixes at home and
   two at work fail), or when any fix sits on or just under a line naming a
   person's place (home, work, office, 住, 屋企 ...). On a merge, the
   RESULTING table is judged against each parent separately, so two branches
   that each pass cannot assemble one that does not. Obvious placeholders
   pass: `example.com`, `555-01xx`, runs of one digit, all-zero UUIDs, every
   value in `identity.example.toml`.
3. Fail closed (#166): UTF-16, undecodable bytes, a symlink, a submodule and a
   blob over 8 MiB are ERRORs, never skips. A declared binary is opened, not
   skipped: its printable strings, PNG text chunks (`tEXt`, and `zTXt`/`iTXt`
   decompressed, UTF-8), and the members of zip containers (xlsx, docx, jar)
   and gzip, bzip2 and xz payloads, two containers deep. A corrupt container,
   or one that decompresses past `NORA_PII_DECODE_MAX` (32 MiB), is an ERROR.

**Only what the change adds.** A value on an added line counts when it occurs
there more often than on the lines the change removed, against every parent.
The residue already in the tree (the scrub track's inventory, #167) does not
block an unrelated edit; adding one more occurrence does.

**Declarations** live in `.githooks/pii-allowlist.txt`, each with a reason:
`path-exempt`, `binary` (opened as above instead of refused), `symlink`,
`gitlink`, `large`, `coords <path> sha256=<digest>` (a location table pinned
to its content: `python3 scripts/pii_scan.py declare-coords <path>` prints
the line), `value` and `value-re` (placeholders). They are read from the
newest ancestor the remote ALREADY HAS, never from HEAD or from the range
being pushed: commit the declaration, push it, then commit what it covers. A
declaration in the same commit as its payload, amended into it, or pushed in
the same push, exempts nothing (#283 criterion 3).

**Deciding a finding that is not a denylist hit.** A shape or judge finding
blocks the commit until someone decides it. Two ways, both recorded:

- **The adjudicator** (the default route, and the one a coding agent takes):

  ```bash
  python3 scripts/pii_adjudicate.py      # then re-run the SAME git commit
  ```

  A headless Claude Code agent (`claude -p`, Sonnet unless
  `NORA_PII_ADJUDICATOR_MODEL` says otherwise) reads the staged change and
  the repository's history -- read-only: `--restricted --safe-mode`, no MCP
  servers, Read/Grep/Glob and Bash for read-only `git` subcommands only,
  every other permission denied -- and answers per finding `real`, `fake`
  or `unsure` with a one-line reason and the evidence it looked at. The
  verdicts are bound to the staged tree (`$GIT_DIR/nora-pii-adjudication.json`):
  only `fake` becomes an acknowledgement, written as `PII-Ack: <id> (agent
  <model>: fake, <reason>)`; `real` and `unsure` stay blocked; staging
  anything else voids them. It refuses to run while a denylist hit or an
  ERROR is open, and a reason or trailer that quotes an identifier-like value
  is withheld. The agent reads the repository as any coding agent here does;
  it is never given a denylisted value. Measured on 24 synthetic cases x 3
  runs (agent-judge spike, 2026-09-26): Sonnet 92% correct, 0 real values
  cleared as fake, median 20 s per case; Haiku 83% with 2 leaks; local models
  38-67%.
- **By hand**, for a finding you have looked at yourself:

  ```bash
  NORA_PII_ACK=1a2b3c4d5e6f,0f9e8d7c6b5a git commit ...
  ```

The `commit-msg` hook writes a `PII-Ack: <id>` trailer for each, so the
decision is in the history, and pre-push honours exactly those trailers. The
id hashes the path and the line: it acknowledges that line and nothing else.

**The optional commit judge** (`NORA_PII_JUDGE=laya|jev`, off by default;
`scripts/pii_judge.py`) runs at `commit-msg`, where the message and the staged
change are both known, and reads the COMMIT: the message lines and each file's
added lines with two lines of context, every line holding a denylisted value
replaced by `[withheld]`, lockfiles and generated files left out. It can RAISE
a finding (at the line it points to), never clear one; an unreachable or
failing model is an ERROR.

| env | what it does |
|---|---|
| `NORA_PII_JUDGE=laya` | Laya's typed-decisions checkpoint on Apple Silicon (`laya-mlx` in the interpreter `NORA_PII_LAYA_PYTHON`; `NORA_PII_LAYA_MODEL`/`NORA_PII_LAYA_SUBFOLDER` to point elsewhere, offline unless `NORA_PII_LAYA_ALLOW_DOWNLOAD=1`). The commit is cut into windows that fit its budget; one yes/no per window, a Choice to point at a line. Local. Threshold 0.29. |
| `NORA_PII_JUDGE=jev` | TypeSafe's hosted `jev-1.13.0`: one request per file (message + that file), an `exists` yes/no decides, a `where` Choice points. **Every rendered line leaves the machine**: not for real repo content until TypeSafe's zero data retention is confirmed. Threshold 0.57. |
| `NORA_PII_JUDGE_THRESHOLD` | overrides the threshold. |

What the numbers are worth. Both thresholds come from the commit-judge spike's
synthetic dev split (66 generated commits): Jev made 0 errors in 3 runs on dev
and held-out (0 in 22 held-out still allows about 14%); Laya made 4 errors in
22 held-out and missed personal data in commit messages and in Cantonese. On
this repo's 55-case set (`scripts/pii_judge_eval.py`, each line in a small
commit) Laya at 0.29 made 13 errors (6 false positives, all on placeholders;
7 misses). Laya's score is a max over windows, so it rises with commit size:
synthetic code-only commits of 500 and 5,000 lines scored 0.34 and 0.43 and
were flagged. Cost on an M-series Mac with Laya on: a 50-line commit 1.1 s
(0.4 s off), 500 lines 2.1 s, 5,000 lines 12.9 s; load 0.1-0.3 s, peak
1.6-1.8 GB. A model can say whether a commit LOOKS like it adds personal data
copied from real records; it cannot know whether a name belongs to a real
person. The denylist is the ground truth for identity.

**What it does not catch.** A headerless integer-microdegree table in a file
that never names coordinates; geohashes; two-decimal coordinates in a file
that never names coordinates; a number split across string concatenation or
a phone number with more than four separator characters between its digits;
a bare local number (8-15 digits) on a line with no phone word, and E.164
without the `+` outside the ranges above; one-word names under 5 letters
(left out of the denylist because they collide with ordinary words);
Chinese characters outside OpenCC's single-character table; encodings other
than those listed (quoted-printable, ROT13, a value split across lines); and
anything an author disguises on purpose. It stops a corpus pasted as it came
out of a database — how the one real incident happened — and it is not what
decides a public release (#167 does).
