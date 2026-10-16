"""`InMemoryStandingStore` — the domain-only double for
`nora.infra.sql.standing.SqlStandingStore`, bound to it by
`tests/contract/test_standing_store_contract.py` so the two cannot drift.

**It refuses what its production sibling refuses**, which for this tier means
one thing and it is the ceiling: `Standing.__post_init__` raises on an
over-long body and `standing_body_fits_the_ceiling_check` raises on the same
input, so a fake that stored whatever it was handed would turn a missing
guarantee into a passing gate. It gets that for free by holding `Standing`
objects rather than dicts — which is the point of holding them.

The dict is keyed `(platform, conversation_id)`, the row's own primary key, so
"write twice, read once" answers the second write here for the same structural
reason `ON CONFLICT DO UPDATE` does in Postgres.
"""

from __future__ import annotations

from nora.domain.standing import STANDING_BODY_CEILING_CHARS, Standing


class InMemoryStandingStore:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], Standing] = {}

    async def read(
        self, *, platform: str, conversation_id: str
    ) -> Standing | None:
        return self._rows.get((platform, conversation_id))

    async def write(self, *, standing: Standing, source: str = "fold") -> None:
        """Mirrors `standing_body_fits_the_ceiling_check`, and the check is NOT
        redundant with `Standing.__post_init__`.

        The constructor refuses an over-long body, so for any caller that builds
        a `Standing` normally this branch is unreachable — which is why the
        contract suite needs it to tell this double apart from its SQL sibling.
        The database CHECK exists for a writer that got a `Standing` into a bad
        state some other way; a fake without the same refusal is MORE
        PERMISSIVE THAN POSTGRES, so a defect that the real store rejects
        passes here
        (`test_the_store_itself_refuses_a_body_over_the_ceiling` exercises it).

        **`btrim` vs `strip()` is a REAL divergence.** One-argument `btrim`
        strips U+0020 SPACES ONLY, not ASCII whitespace: `length(btrim(E'\t'))
        = 1` and `length(btrim(E'\n\n')) = 2`, where `length(btrim('   ')) = 0`.

        So `standing_body_fits_the_ceiling_check` ACCEPTS a tab-only or
        newline-only body and this double REFUSES it (`str.strip()` strips all
        Unicode whitespace). The two are not interchangeable on that input.

        Kept, because the direction is the safe one — a double that refuses a
        superset can never let through something the real store would reject.
        And it is unreachable through the type: `Standing.__post_init__`
        refuses a blank body first, so only the `object.__setattr__` smuggling
        in `test_the_store_itself_refuses_a_body_over_the_ceiling` can
        construct it. The contract suite rests on the two refusing IDENTICALLY,
        which holds for length and for `fold_count` but not for whitespace.
        """
        if (
            not standing.body.strip()
            or len(standing.body) > STANDING_BODY_CEILING_CHARS
        ):
            raise ValueError(
                f"standing_body_fits_the_ceiling_check: body is "
                f"{len(standing.body)} characters"
            )
        if standing.fold_count < 0:
            raise ValueError("standing_fold_count_is_not_negative_check")
        # `working_set` needs no divergence check the way `body` does above:
        # `Standing.__post_init__` already refuses an over-ceiling
        # `working_set` the same way it refuses an over-ceiling `body`, and
        # unlike `btrim` vs `strip()` there is no known gap between that
        # check and `standing_working_set_fits_the_ceiling_check` — holding
        # `Standing` objects (not dicts) is what makes that refusal free here.
        self._rows[(standing.platform, standing.conversation_id)] = standing
