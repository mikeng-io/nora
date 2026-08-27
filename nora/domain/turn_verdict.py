"""`TurnVerdict` — one row per TURN, never rewritten: the debrief's judgement
about whether the turn it just reviewed was GROUNDED
(`design/dreamer-agent-loop/26-standing.md` §3, spec §6.3).

**Why this is a separate type from `Standing`, and why that is structural
rather than tidiness.** The split was found by a bug. The first draft put these
per-turn fields on the per-conversation row — and §16.4's re-derivation trigger
reads `grounded`, a flag *the next turn had already overwritten*. **A per-turn
fact on a row revised every turn is a fact with a lifetime of one turn.**

So the cardinalities are opposite by construction:

| type | cardinality | rewritten? |
|---|---|---|
| `Standing` | one row per **conversation** | **yes — in place** |
| `TurnVerdict` | one row per **turn** | **never** |

`grounded` is the trigger's input, and `unsupported` is what makes a `False`
actionable rather than an accusation: a verdict that says "this turn was not
grounded" and cannot say WHICH claims were unsupported is a judgement nobody
can check, which is the shape `09-measurement.md` refuses. Empty when
`grounded` is True — and `__post_init__` refuses the contradiction, because a
turn cannot be both grounded and carrying unsupported claims.

Framework-free, per `nora/domain/`'s rule: stdlib and dataclasses only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: The cap on one `unsupported` entry. These are model-authored strings that
#: ride into `turn_verdict` on every ungrounded turn, and the tier that reads
#: them back is a re-derivation pass over many turns at once — so an unbounded
#: entry is a per-turn cost paid again on every rebuild. The number matches
#: `nora.domain.notes.DESCRIPTION_MAX_CHARS` deliberately: both are one
#: model-authored sentence naming one thing, and two different limits for the
#: same shape is a number somebody has to look up.
UNSUPPORTED_MAX_CHARS = 200

#: The cap on how many unsupported claims one verdict may carry. A turn that
#: produced more than this many distinct unsupported assertions is not a turn
#: with a long list — it is a turn that went wrong in a way the LIST cannot
#: express, and truncating silently would report the first ten as though they
#: were all of them.
UNSUPPORTED_MAX_ITEMS = 10


@dataclass(frozen=True, slots=True)
class TurnVerdict:
    """The debrief's per-turn judgement. Written once, never revised.

    `tag_id` is the natural key rather than a synthetic id: exactly one verdict
    exists per turn by definition, and a surrogate key would make "two verdicts
    for one turn" representable — which is precisely the state the
    never-rewritten property exists to forbid. The database enforces it as the
    PRIMARY KEY, so the guarantee is DDL rather than convention: an UPDATE path
    that does not exist cannot be taken by a future writer who did not read
    this docstring.
    """

    tag_id: str
    conversation_id: str
    grounded: bool
    unsupported: tuple[str, ...] = field(default_factory=tuple)
    created_at: int = 0

    def __post_init__(self) -> None:
        if not self.tag_id.strip():
            raise ValueError("a TurnVerdict needs the tag_id of the turn it judges")
        if not self.conversation_id.strip():
            raise ValueError("a TurnVerdict needs a conversation_id")
        if self.grounded and self.unsupported:
            raise ValueError(
                "a grounded turn cannot carry unsupported claims — "
                f"grounded=True with {len(self.unsupported)} unsupported "
                "entries is a contradiction, not a partial verdict"
            )
        if len(self.unsupported) > UNSUPPORTED_MAX_ITEMS:
            raise ValueError(
                f"{len(self.unsupported)} unsupported claims is over the "
                f"{UNSUPPORTED_MAX_ITEMS} a verdict may carry; a turn with "
                "more went wrong in a way this list cannot express"
            )
        for claim in self.unsupported:
            if not claim.strip():
                raise ValueError("an unsupported claim cannot be blank")
            if len(claim) > UNSUPPORTED_MAX_CHARS:
                raise ValueError(
                    f"an unsupported claim is {len(claim)} characters, over "
                    f"the {UNSUPPORTED_MAX_CHARS}-character cap"
                )
