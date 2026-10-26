# 0029 · A prompt block names its subject and its author by id

- **Status:** proposed (2026-10-25). The operator approved the format the same day, after
  three blind rounds, and required ids, not aliases.
- **Date:** 2026-10-25
- **Supersedes:** [ADR 0026](0026-the-character-tier-names-its-entry-parts-profile-traits-hypotheses.md),
  whose names were not tested on a reader and whose rename was never done
- **Index:** a DEC number is taken at merge

## Context

ADR 0026 chose its names by argument: Character for the tier, entry for a row, and
profile / traits / hypotheses for the parts. The operator still could not tell what they
meant without an explanation. His rule (2026-10-25) is that a name neither a model nor a
human understands without extra explanation is already wrong.

So the names were measured instead of argued.

**Method.** One block was shown with no description and no surrounding prompt, about a
synthetic person, a room, and Nora herself.
- **Reader:** the production `nora` model alias.
- **Judge:** `deepseek-v4-pro`, grading against a fixed answer key.
- **Questions:** what is it about, who wrote it, how reliable is it, is it an
  instruction, and is it happening now. Round 3 added a sixth: which messages did the
  subject write.
- **Runs:** 5 per block in round 1, 8 in rounds 2 and 3.

The script and raw answers for each round live on the deploy host
(`/tmp/naming_test*.py`, `/tmp/naming-results*.json`). They contain synthetic subjects
only.

| round | block | score | "who wrote it" right |
|---|---|---|---|
| 1 | live prose (`<character><person_account>`, `<culture>`, `<self>`) | 67/75 | 7/15 |
| 1 | `<persona>` | 66/75 | 7/15 |
| 1 | ADR 0026 names | 65/75 | 6/15 |
| 2 | `<profile of="Ada">` | 97/120 | 4/24 |
| 2 | `<profile of="Ada" written_by="you">` | 116/120 | 21/24 |
| 3 | the same, read by a sub-agent | 104/144 | 6/24 |
| 3 | `<profile of="ada" written_by="nora">`, messages `from="ada" name="Ada"` | 142 and 141/144 | 24/24, both readers |
| 3 | the same + `source="what people said in chat"` | 142 and 143/144 | 24/24, both readers |
| 3 | ids with no display names | 136 and 142/144 | 24/24, but Nora linked the room's messages 18/24 |

Three findings:

- **Authorship was the failure, not the noun.** With no author stated, every variant was
  read as the subject's own words. Nora took her own guesses for Ada's claims. Once the
  tag said who wrote it, every noun tested scored within a few points of the others.
- **"You" depends on who is reading.** A sub-agent handed the block read
  `written_by="you"` as itself. It then found "no messages by Smith in the room" for
  Nora's own entry. An id means the same thing to every reader.
- **An id needs the name next to it where people speak.** With ids alone, Nora decided
  only her own `from="nora"` message had been posted in the room.

The chat formats every model is trained on already work this way: each message carries
its author as a role (`system`, `user`, `assistant`), never as a pronoun. Anthropic's
prompting guide recommends XML tags for exactly this, so the model can tell parts of the
input apart. This decision gives the blocks inside a turn the same property.

Today's code does neither:
- Nora's own entry is keyed `self` and rendered `of="you"`.
- Nora has no id; `identity.toml` lists only her platform handles.
- A message carries only a display name.

`nora/agent/prefill.py` also reads R-18 as keeping every id out of the prompt. R-18
forbids identifiers leaving in a reply: E.164 numbers, UUIDs and key-shaped strings.
A `person_id` is a slug the operator writes in `identity.toml`, not a platform
identifier, so it may appear in the prompt.

## Decision

**Every block in the prompt says, in its tag, who it is about and who wrote it, by id.**

```
<profile of="ada" written_by="nora" source="what people said in chat" written="3d ago">
facts:
- …
traits:
- …
guesses (unconfirmed):
- …
</profile>
<m n="1" from="ada" name="Ada" age="5m">…</m>
```

1. **Ids, never aliases.**
   - A person is their `person_id`. A room or unit is its id in `policy.toml`.
     Nora is `nora`.
   - `you`, `self` and `me` are never an identity in a tag.
   - Nora gets the id `nora`, and her system prompt states it.
2. **The author is always explicit.** `written_by` is on every block that somebody wrote.
   Where the writer is Nora's own background pass, the value is `nora`.
3. **Messages carry the id and the name.** The form is `<m from="<person_id>" name="<Name>">`.
   The id links a message to every block about that person; the name is what people
   actually call each other.
4. **One name everywhere.** What the model reads is what the code, the tables, the config
   and the docs call it.
   - The Character tier and its "entry" become **profile**: one profile of one person,
     room, unit or Nora.
   - Its parts are **facts**, **traits** and **guesses**. The model sees
     `guesses (unconfirmed)`; uncertainty goes in the name, not in a description.
   - **Persona**, **Character** (as a tier name), **entry**, **account** (outside platform
     accounts) and **hypotheses** are retired.
   - The told / deduced / induced / abduced modes are internal and are not renamed here.
5. **No metaphors or architecture words in the prompt.** A tag says what is inside it.
   The structure of the system (tier, layer, Notebook) stays out of the prompt.
6. **Blocks about to be deleted are not renamed.**
   - The live prose blocks (`<character>`, `<person_account>`, `<culture>`,
     `<unit_culture>`, `<self>`) and their descriptions stay byte-identical until the
     switch deletes them.
   - Renaming them would change live behaviour for a block that is about to go away.
7. **The blind test is a permanent check.** Any new block, and any renamed block or
   attribute, passes the blind test before it ships. "Passes" means both readers (Nora,
   and a sub-agent handed the block) answer "who wrote it" and "what is it about"
   correctly, and it scores no worse than the block it replaces. The test becomes an eval
   in `evals/` with this ADR's answer key.

## Consequences

**Better**

- A reader that knows none of the project's vocabulary can tell what a block is, who wrote
  it, and how far to trust it. That is the bar `nora/domain/block_types.py` already
  states.
- The same block can be handed to a sub-agent without changing its meaning.
- One name runs from the prompt to the table, so a grep for it finds everything.

**Worse**

- **More to rename than ADR 0026 planned.** The rename now covers Character and entry
  as well as persona. 1,469 lines under `nora/` say "persona" (counted
  2026-10-25 with `git grep -c -i persona -- nora`), and `evals/` uses the same
  name in its file names.
- **Messages get longer.** `from=` is added to every message, a few tokens per message on
  every turn.
- **Nora becomes a person-like id she never had.** Anything that keys on `self` has to
  change.
- **The test is small.** One reader model, one judge, synthetic content, and n=8. It
  separates names; it does not prove behaviour in live rooms.
- **Untested names.** Other blocks (standing, memos, lessons, notes, rollup) and the
  proposed `observations` name for message readings have not been measured yet. They are
  audited the same way before they are renamed.

## Rejected

- **ADR 0026's names (Character, entry, profile / traits / hypotheses).** They scored no
  better than the live prose, and they needed the glossary to explain them.
- **`written_by="you"`.** Its meaning changes with the reader: the sub-agent scored 6/24 on
  "who wrote it".
- **`<your_notes on=…>`.** It tied for best in round 2 but collides with the existing
  `<notes>` block.
- **Ids with no display name on messages.** Nora lost which messages belonged to the room.
- **Explaining the names in block descriptions instead.** The tests had no descriptions.
  Names that need a description fail the operator's rule, and descriptions are where the
  vocabulary drift came from.
