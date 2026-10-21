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

- **`commit-msg`** — scans the message and the author/committer identity for
  identifiers (below) FIRST, then runs `scripts/check_commit_subject.py`. The
  closed type list and the authored-merge rule are enforced here so the
  convention stops depending on memory. `--no-verify` bypasses it; if you use
  that, name the reason in the commit body. The order matters: the subject
  checker prints a bad subject as written, so it runs only once the scan has
  passed (it once printed a denylisted name the scan would have masked).
- **`pre-commit`** — scans the STAGED blobs for identifiers (below).
- **`pre-push`** — the identifier scan over everything the push gives the
  remote, then (only if it passed) the same subject checker across EVERY
  commit in the pushed range, not just the head: the commits that need it
  most are the ones made before the hook was installed. Capped at 200 per
  ref, and it says so when the cap binds.
- **Every hook runs this repo's scanner and nothing else.** A symlink at
  `scripts/`, at the scanner, at its one import (`pii_cjk_fold.py`) or at the
  subject checker is refused; `python3 -I -S` keeps `PYTHONPATH`,
  `PYTHONSTARTUP`, the user site and `.pth` files from substituting a module;
  and the scanner neither writes nor reads a bytecode cache (it used a shared
  `$TMPDIR/nora-pii-pycache`, where a planted `.pyc` ran in place of the
  source).

The rule these enforce lives in `CONTRIBUTING.md` §Commit messages and
`docs/maintainers/conventions.md` §Merge strategy. The checker is the typing aid; the doc is the contract. To change
either, change both in one commit.

## Identifiers (#283)

`scripts/pii_scan.py`, stdlib `python3`. It judges what a commit ADDS: the
staged blobs against HEAD (and MERGE_HEAD in a merge) at `pre-commit`; the
WHOLE message file (comment lines and text past the scissors included: `git
commit -m` and `--cleanup=verbatim` keep them) and the author/committer
identity at `commit-msg`; and at `pre-push` every blob, message, identity,
extra commit header (a `mergetag`'s tag message included), tag message and
tagger that the push gives the target remote, and the pushed ref names (a
branch or tag named after a person is published too; denylist only, printed
as `<ref 1a2b3c4d5e>` when it matched). Every NEW path -- added, renamed or
copied, directory names included -- goes through the same denylist and shape
rules as content; a path that matched is printed only as an opaque
`<path 1a2b3c4d5e>`. Nothing is judged by a model: every verdict is a rule.

The push scan asks the TARGET remote what it already has (`git ls-remote`)
rather than trusting local remote-tracking refs, which go stale (a branch
deleted on the remote), can be written by anyone (`git update-ref`) and
describe a different remote when you push somewhere else. `git ls-remote`
never prompts (no terminal or GUI credential prompt, ssh in batch mode) and
times out after 20 s; a remote it cannot reach is an ERROR. Pushing to a
remote that has nothing yet scans the whole history, up to
`NORA_PII_PUSH_MAX`=1000 commits; past that it is an ERROR, not a partial
look.

**Cutover to a new remote** (the scrubbed public repository): the whole
history is "new" there, so its residue can never pass. Instead of
`--no-verify`, set `NORA_PII_PUSH_BASELINE=<manifest>` and
`NORA_PII_PUSH_BASELINE_SHA256=<its full sha256>`. The manifest is the file
the scrub's content verification writes:

```
# nora-pii-baseline v2
remote <the exact url this push goes to>
<full commit id>
...
```

It is bound three ways, and each is an ERROR when it does not hold: it
counts ONLY the commits it lists -- never their ancestors, so a manifest
naming a tip vouches for nothing beneath it, and the verification must list
every commit it read clean; only for the one remote it names (the url
pre-push is given); and only when its digest equals the pinned
`NORA_PII_PUSH_BASELINE_SHA256`, which lives outside the file, so editing the
manifest voids it. A v1 manifest (unbound) is refused. The use is printed
with the digest on every run. The manifest is still a trust decision: it is
only as good as the verification that wrote it. Tags are scanned too: an annotated tag's message and
tagger, and a lightweight tag on a bare blob or tree (every object in it the
remote does not already reach).

Exit 0 PASS, 1 FAIL, 2 ERROR ("could not look": no denylist, a malformed
`NORA_PII_*` setting or private list, an unreadable blob or container, an
unreachable remote, a crash); both non-zero block. The output names path, line and
rule, and masks the value.

**Where the hooks do not run.** `git am`, `git cherry-pick` and `git rebase`
create commits without `pre-commit` (and cherry-pick and rebase without
`commit-msg`; git 2.55's `rebase --continue` too), and `git commit
--no-verify` skips both. A `git commit` YOU run at a rebase or cherry-pick
stop fires both hooks, with HEAD detached and `REBASE_HEAD` /
`CHERRY_PICK_HEAD` set: the hooks then honour the `PII-Ack` trailers of the
commit being replayed (its findings were decided when it was first
committed), find the upstream through the branch being rebased, and honour
a declaration the rebase rewrote (below). Pre-push is the
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
is an ERROR. A machine that genuinely has no access to the real values (an outside contributor or a
fresh CI runner) sets `NORA_PII_DENYLIST_ABSENT=<reason>`; the reason is
printed on every run and only the shape rules apply.

Two more private files, both optional (missing = the feature is off, which
only makes the scan stricter; present but unreadable = ERROR). Neither is
ever printed, and neither belongs in the repository:

- **Keep-phrases**, `~/.config/nora/pii-keep-phrases.txt` (or
  `$NORA_PII_KEEP_PHRASES`): one exact multi-word phrase per line that may
  contain a denylisted value -- the creator credit lines. Before denylist
  matching, each occurrence of a phrase is masked out: the exact phrase, case
  and punctuation as written, any whitespace between its words, not glued to
  a longer word. Anything else on the line is still matched, so the same
  name outside the phrase, or the phrase with its name reordered, recased or
  its wording changed, FAILS. A phrase that is one word, or nothing but the
  denylisted value, is an ERROR: it would switch the value off. The owner's
  own name is no exception -- project identity in code is only the public
  org and domain (`mikeng-io`, `mikeng`, `mikeng.io`, `iam@mikeng.io`); if
  the denylist holds a name that appears in the author line, that line
  (`Name <email>`) belongs here.
- **Pseudonyms**, `~/.config/nora/pii-pseudonyms.txt` (or
  `$NORA_PII_PSEUDONYMS`): one value per line that the scrub map itself
  produced -- known fake. A SHAPE finding whose value is listed is cleared.
  It is asked only after the denylist has had its say on the same digits, so
  it can never clear a denylist hit, nor a shape whose digits sit inside a
  real number.

**What it checks**, in order:

1. The denylist, in every spelling the matcher knows: case; separators
   between digits (up to four characters, so `+<cc> NNNN - NNNN` is one
   number), including Unicode dashes, the minus sign and no-break spaces;
   full-width digits; control characters and every invisible character
   (all format characters -- zero-width spaces and joiners, bidi marks and
   isolates, soft hyphen, word joiner, BOM, the tag block -- plus variation
   selectors, the combining grapheme joiner and Hangul fillers); Cyrillic and Greek
   look-alike letters; Traditional and Simplified Chinese (folded through
   OpenCC's character table) and spaces inside a CJK name; `Family, Given`
   order and a middle initial; dashless and upper-case UUIDs; JID and
   bare-local phone forms; url-safe and percent-encoded base64. Each line is
   also DECODED and matched again: JSON `\uXXXX` and Python `\xNN` /
   `\UXXXXXXXX` escapes, URL percent-
   encoding, HTML entities, and base64/base64url segments (a JWT's payload),
   two layers deep, plus `&#x..` entities without their `;`, hex runs that
   decode to text, combining accents stripped, middle dots inside a CJK name,
   and an address spelled out (`name at host dot tld`); a number spread over
   up to 8 separator characters is found too, when the run is phone-sized.
   Decoding is capped per line (64 segments, 256 KB decoded). A hit is a
   known real value: remove it. It cannot be acknowledged, declared away or
   cleared by a pseudonym; only an exact keep-phrase is masked before
   matching. A shape finding whose digits sit inside a denylisted number is reported
   as that number, not as an acknowledgeable shape.
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
   blob over 8 MiB are ERRORs, never skips. Declaring or acknowledging one
   never suppresses the denylist: whatever can still be read -- ASCII and
   UTF-16 strings, runs of valid UTF-8 (a CJK name in a binary, or in a text
   file with one bad byte), the whole blob as UTF-16 when it is UTF-16, and
   the raw bytes of a container that could not be opened -- is matched
   against the denylist, and a hit is reported beside the ERROR. A declared binary is opened, not
   skipped: its printable strings, PNG text chunks (`tEXt`, and `zTXt`/`iTXt`
   decompressed, UTF-8), and the members of zip containers (xlsx, docx, jar)
   and gzip, bzip2 and xz payloads, two containers deep. One budget covers a
   blob's whole expansion -- `NORA_PII_DECODE_MAX` (32 MiB) decompressed in
   total across every member and level, 2,000 members, 10 s -- and
   `NORA_PII_DECODE_PUSH_MAX` (256 MiB) covers a whole run. A corrupt
   container, or one past any budget, is an ERROR.

**Only what the change adds.** A value on an added line counts when it occurs
there more often than on the lines the change removed, against every parent.
The residue already in the tree (the scrub track's inventory, #167) does not
block an unrelated edit; adding one more occurrence does.

**Declarations** live in `.githooks/pii-allowlist.txt`, each with a reason:
`path-exempt` (lifts the shape and coordinate rules for that file; the
denylist and the path scan still apply -- NO declaration exempts a denylist
hit), `binary` (opened as above instead of refused), `symlink`,
`gitlink`, `large`, `coords <path> sha256=<digest>` (a location table pinned
to its content: `python3 scripts/pii_scan.py declare-coords <path>` prints
the line), `value` and `value-re` (placeholders). A path pattern must be
narrow: a literal path, a directory at least two levels deep (`site/public/*`)
or a named kind (`assets/*.png`); `*`, `**`, a leading wildcard and a bare
wildcard under a top-level directory (`vendor/*`, and `vendor/[!.]*`: a
`[...]` class is a wildcard too) are refused for every path form, `coords`
included. A pattern that matches more than 200 files is an ERROR, counted
over the files the base tracks AND the paths the change touches -- a
declaration published while its directory was empty cannot then cover a
thousand new files -- and the tree audit (`tree --allowlist-from-worktree`)
applies the same cap. A `value-re` that fullmatches any of a fixed set of
generic probe values (numbers of every length under common country codes,
hex ids, mailbox addresses, plain words) is refused as too broad: it must
pin one placeholder range (`(852)?900001\d\d`), not a class (`.*`, `\d+`,
`44\d{10}`). Errors name the line, never the pattern.

Declarations are read from the newest ancestor the remote ALREADY HAS --
an ancestor of a tip it advertises, so a branch behind its remote still
counts -- never from HEAD or from the range being pushed: commit the
declaration, push it, then commit what it covers. A rebase rewrites the
commit that added a declaration; a line the rebased branch still carries
counts when the remote already holds that exact line at one of its tips
(the branch's old tip, typically). At `pre-commit` and `commit-msg` the
"already has" comes from the local remote-tracking refs, so when the
declarations change the outcome the hook asks the remote (`git ls-remote`)
and redoes the scan on what it advertises; unreachable, the strict result
stands. A declaration in the same commit as its payload, amended into it,
or pushed in the same push, exempts nothing (#283 criterion 3).

`scrub/replacements.txt` stays declared `path-exempt`, which since this change
no longer lifts the denylist: an edit that ADDS a real value to it is refused
like any other. The map is private by design and belongs outside the
repository (scrub/README.md).

**Deciding a finding that is not a denylist hit.** The scan is
deterministic: no model judges anything (the agent adjudicator and the
optional commit judge were removed; their trailers, `PII-Ack: <id> (agent
...)`, clear nothing). A shape finding clears in exactly three ways:

- **A declaration** in `.githooks/pii-allowlist.txt` that the remote already
  has (above): narrow, capped, never in the change it covers.
- **A pseudonym**: the value is in the private registry (above), because the
  scrub map produced it.
- **A human who has looked at the value**, by id:

  ```bash
  NORA_PII_ACK=1a2b3c4d5e6f,0f9e8d7c6b5a git commit ...
  ```

  The `commit-msg` hook writes a `PII-Ack: <id> (human)` trailer for each,
  so the decision is in the history, and pre-push honours exactly those
  trailers. An id belongs to ONE finding (path, line, rule and the matched
  value): it acknowledges that finding and not its neighbour on the same
  line. A coding agent never acknowledges a value it has not had a human
  check.

**Acknowledgements are trust-based, and the trailer is a claim**: anyone can
type one. Nothing local can stand in for it -- no file in the git dir clears
a finding (the adjudicator's verdict file and log are gone; the side file
pre-commit leaves for commit-msg only carries ids it already applied from
`NORA_PII_ACK`, and writing it by hand changes no verdict). None of this is
a guarantee. **The denylist is the only hard guarantee**: its hits can be
neither acknowledged nor declared away, and only an exact keep-phrase is
masked before it matches.

**What it does not catch.** A headerless integer-microdegree table in a file
that never names coordinates; geohashes; two-decimal coordinates in a file
that never names coordinates; a number split across string concatenation or
a denylisted number spread over more than 8 separator characters;
a bare local number (8-15 digits) on a line with no phone word, and E.164
without the `+` outside the ranges above; one-word names under 5 letters
(left out of the denylist because they collide with ordinary words);
Chinese characters outside OpenCC's single-character table; encodings other
than those listed (quoted-printable, ROT13, punycode, a value split across
string concatenation); an LFS file's content; and
anything an author disguises on purpose. It stops a corpus pasted as it came
out of a database — how the one real incident happened — and it is not what
decides a public release (#167 does).
