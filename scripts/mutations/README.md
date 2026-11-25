# Mutation tables

One table per change, named after the branch or the issue. A table names exact
source text, so it **rots as soon as that code moves** — that is expected. It is
the record of what a branch proved, not a gate that runs forever.

    python scripts/mutate.py scripts/mutations/<name>.json
    make mutate TABLE=scripts/mutations/<name>.json

Every Python phase runs ONE command — this interpreter's
`python -m scripts.pytest_gate`, the same shared host lease `make test` and
`make gate` use; never `uv`, never raw `pytest`. A held lease is waited out,
and one that never frees makes the entry `UNAVAILABLE` — not red, not caught.

A mutant counts as caught only when pytest exits 1 with a real failure count,
no error count, and the restored source is verified green — a failed test
beside any setup or teardown error is `BROKEN ENTRY`, never a catch. A run
whose tests error at import or collection is `BROKEN ENTRY`, a selector that
ran nothing is too, and a collection exit 2 without the gate's own busy or
stale-lease message is an error run, not contention — a nonzero exit by
itself never proves anything. `ALL MUTATIONS RED AND RESTORED` prints only
when every selected entry reached that state; each RED is an observed test
failure, and confirming it is the intended assertion is a manual step the
runner never claims for you.

Each phase's full stdout/stderr, command, exit code and counts land in a
private proof directory outside git (`~/.local/share/nora/mutation-proofs/`,
or `--proof-dir`) — directories 0700, files 0600; each phase receipt records
its actual HEAD and the mutated file's sha256, and `manifest.json` records
HEAD, the table's hash, each entry's anchor count and the sha256/git-diff
restoration evidence. The path prints at the end of the run.

`make gate` does not RUN the tables, but it does check that they still ANCHOR:
`tests/architecture/test_mutation_tables_anchor_to_their_source.py` fails when
any entry's `old` no longer occurs exactly once in its `file`, when `new` equals
`old`, or when the file or test it names is gone. A refactor that moves guarded
code therefore has to re-anchor the entry (same intent), re-point it at the
code's new home, or delete it and say why in the commit. It does not say the
named test still goes red; only `make mutate` does.

Write the table while doing the work — one entry per fix you intend to claim —
and run it before reporting. `make gate` green says the suite passes; the sweep
says the suite would have NOTICED the bug you just fixed. Reporting the second
alongside the first is the point.

`media-comprehension.json` is the worked example: 26 entries from issues
#116/#117, the branch the practice came out of. See `scripts/mutate.py`'s
docstring for why, and `design/19-media-comprehension.md` for the war-story.

An entry is five fields:

| field | what it is |
|---|---|
| `name` | what this fix is, in a few words |
| `file` | repo-relative path |
| `old` | the text **as committed** — must appear exactly once |
| `new` | that text **as it was before the fix** |
| `target` | pytest node IDs separated by spaces, or a `go:` target |

A good entry reverts behaviour and nothing else. Three ways to write a bad one,
all of which the runner reports as `BROKEN ENTRY` rather than as a pass:

* a selector that matches nothing (pytest exits 5 — «no tests ran», not «failed»)
* a replacement that raises before the reverted behaviour executes, e.g. a name
  the module does not import, which some `except` then swallows
* an anchor that appears more than once, so the mutation lands somewhere else
