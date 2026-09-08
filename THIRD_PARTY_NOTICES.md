# Third-party notices

Work included in this repository that originates elsewhere, with the notices
its licence requires retained.

Nothing here is a dependency — `pyproject.toml` is where those live. This file
covers material **copied into the tree**, which is a different obligation: a
dependency carries its own licence with it, and a copy does not.

---

## graphiti-core — entity-resolution prompt

> **REMOVED FROM THE TREE 2026-09-08.** The file below was deleted with the
> knowledge tier (issue #115, commit `ea165f57`), so nothing in the current
> working tree carries this material. **The notice is retained, not deleted:**
> the obligation attached to the versions that shipped it — V4 among them — and
> a licence record for a released artifact does not stop being true because the
> next release drops the file. Delete this section only if every release
> carrying it is itself withdrawn.

**Used in:** `nora/dreamer/objectives/fact-entity-resolution.md` (deleted
2026-09-08; resolves in git history only)

**Source:** `graphiti_core/prompts/dedupe_nodes.py` from
<https://github.com/getzep/graphiti>, read at `main` on 2026-08-28 (225 lines).

**Licence:** Apache License 2.0

```
Copyright 2024, Zep Software, Inc.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```

### What was taken, and what was not

**Taken: the prompt's reasoning and its four worked examples.** That is the
load-bearing part and it is corpus-independent —
`design/dreamer-agent-loop/25-knowledge-pipeline.md` §4.1: `NYC`/`New York
City` (an abbreviation), `Java` the language against `Java` the island (one
name, two real-world things), `Marco's car`/`Marco's vehicle` (a synonym with
the same possessor), and `Sam`/`Sam` (the same person referred to again). Three
of the four are this room's hard cases exactly, and no corpus we have would
have produced better ones.

Taken with it: the instruction that entities are duplicates only when they
refer to *the same real-world object or concept*, the two explicit
never-mark-as-duplicate conditions, and — the part that matters most — **the
"or you are unsure" branch**, which is `21-substrate-contract.md` §5's
default-to-split already written into the upstream wording.

**NOT taken: `dedup_helpers.py`, the matcher.** Deliberately, and the reason is
measured rather than argued (`25` §4.1): its fuzzy path is ASCII-only —
non-ASCII normalises to the empty string and `_jaccard_similarity(∅, ∅)`
returns 1.0 — and its `_FUZZY_JACCARD_THRESHOLD` of 0.9 misses `Big
Walk`/`Big Walks` at 0.833. Our candidate query is our own.

**NOT taken: the file itself.** No source file was copied. The prompt was
re-authored as an objective in this repo's own format, because two things about
upstream's shape do not survive contact with this system:

1. Upstream's `node_list` prompt echoes **UUIDs**. `nora.agent.dream_screen`
   drops any envelope item carrying an identifier, so an adaptor built on that
   shape would fail silently while its counters looked healthy. Our version
   uses ordinal handles throughout, which upstream's `node` prompt already does
   with its integer `candidate_id`.
2. The objective carries this repo's frontmatter, its refusal discipline, and
   its own return contract.

### The reversal condition

`16-open-items.md` §11: there is no graded corpus for this room, so **we cannot
currently tell whether this prompt beats one we write.** Adopting it is a
judgement about its worked examples, not a measurement. When the corpus exists,
measure both; if ours wins, this notice stays only as long as any of the
wording does.
