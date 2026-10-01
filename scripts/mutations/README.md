# Mutation tables

One table per change, named after the branch or the issue. A table names exact
source text, so it **rots as soon as that code moves** — that is expected. It is
the record of what a branch proved, not a gate that runs forever.

    uv run python scripts/mutate.py scripts/mutations/<name>.json
    make mutate TABLE=scripts/mutations/<name>.json

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
| `target` | the pytest selector that is supposed to guard it |

A good entry reverts behaviour and nothing else. Three ways to write a bad one,
all of which the runner reports as `BROKEN ENTRY` rather than as a pass:

* a selector that matches nothing (pytest exits 5 — «no tests ran», not «failed»)
* a replacement that raises before the reverted behaviour executes, e.g. a name
  the module does not import, which some `except` then swallows
* an anchor that appears more than once, so the mutation lands somewhere else
