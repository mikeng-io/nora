"""`CultureAdaptor` — the culture tier's only moving part (design/culture-tier).

Written around the places this adaptor can be wrong without anything saying so:
it can hand the model a chronicle and then ask for an account, it can pick
arbitrarily between two proposed accounts, it can append a link carrying no
information, and it can fire in a room that said nothing.

Since 2026-09-17 it can also be wrong in two new ways, and both are silent:
it can be asked for a join and handed one table (`design/dreamer-agent-loop/
03-no-agent-loop §7`, `17-failure-classes §11` — the tautology that passes every
gate the runtime owns), and it can hold a character cap no budget can emit,
which truncates the completion, holds the watermark and re-reads the same batch
forever.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from nora.config import DreamBudgetConfig
from nora.domain.culture import (
    ACCOUNT_OUTPUT_TOKENS,
    BODY_MAX_CHARS,
    OUTPUT_TOKENS_PER_CHAR,
    CultureProposal,
    CultureRevision,
)
from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.domain.notes import Note
from nora.domain.ports import Proposal
from nora.domain.standing import Standing
from nora.language.culture import (
    CULTURE_ADAPTOR_NAME,
    CULTURE_OUTCOMES,
    CULTURE_TOOLS,
    STORE_CALLS_PER_TICK,
    AccountNotRecordableError,
    CultureAdaptor,
)
from nora.obs.metrics import Metrics

T0 = 1_757_000_000_000
KEY = "discord/g1"


class _Store:
    def __init__(self, head: CultureRevision | None = None) -> None:
        self._head = head
        self.appended: list[dict] = []

    async def head(self, *, key: str) -> CultureRevision | None:
        return self._head

    async def append(self, **kw: object) -> CultureRevision:
        self.appended.append(kw)
        return CultureRevision(
            id="new", culture_key=str(kw["key"]), body=str(kw["body"]),
            written_at=int(kw["written_at"]),  # type: ignore[arg-type]
            established_by=str(kw["established_by"]),
        )


class _StandingStore:
    """The per-room `standing` tier, keyed as the real one is."""

    def __init__(self, rows: dict[tuple[str, str], Standing] | None = None) -> None:
        self.rows = rows or {}
        self.reads: list[tuple[str, str]] = []

    async def read(
        self, *, platform: str, conversation_id: str,
    ) -> Standing | None:
        self.reads.append((platform, conversation_id))
        return self.rows.get((platform, conversation_id))


class _Notes:
    """The note tier's index, keyed by WORKSPACE (DEC-105)."""

    def __init__(self, notes: tuple[Note, ...] = ()) -> None:
        self._notes = notes
        self.reads: list[str] = []

    async def standing(self, *, workspace: str) -> tuple[Note, ...]:
        self.reads.append(workspace)
        return self._notes


def _standing(body: str = "they ask me for routes and nothing else") -> Standing:
    return Standing(
        platform="discord", conversation_id="g1", body=body,
        fold_count=1, updated_at=T0,
    )


def _note(title: str = "dinner on the 20th") -> Note:
    return Note(
        id="n1", workspace="friends", title=title,
        description="where, still open", uncertainty="where, still open",
        closing_condition="somebody books it", established_in="discord/g1",
        created_at=T0, head_revision_id="nr1", anchor_date=20260920,
    )


def _adaptor(
    store: _Store,
    metrics: Metrics | None = None,
    *,
    standing: _StandingStore | None = None,
    notes: _Notes | None = None,
    label: str | None = "Zone 42",
    description: str | None = "the games channel",
    workspace: str | None = "friends",
    standing_key: tuple[str, str] | None = ("discord", "g1"),
) -> CultureAdaptor:
    return CultureAdaptor(
        culture=store, standing=standing, notes=notes, metrics=metrics,
        workspace_for=lambda _k: workspace,
        standing_key_for=lambda _k: standing_key,
        label_for=lambda _k: label,
        description_for=lambda _k: description,
    )


def _row(body: str, *, ts: int = T0, name: str = "Ada", rid: int = 1) -> TranscriptRow:
    return TranscriptRow(
        id=rid, sender_id="u1", sender_name=name, person_name=name, ts=ts,
        body=body, is_nora=False, delivery_status=DeliveryStatus.SENT, raw_json="{}",
    )


def _revision(body: str) -> CultureRevision:
    return CultureRevision(
        id="r1", culture_key=KEY, body=body, written_at=T0,
        established_by="culture",
    )


def _proposal(body: str) -> Proposal:
    return Proposal(
        payload=CultureProposal(body=body), instruction_sha256="a" * 64,
        model="flash", established_by="culture-run-1",
    )


# --- inputs: a quiet room costs nothing ------------------------------------


async def test_an_empty_batch_renders_NOTHING_so_the_runtime_does_not_fire():
    """`DreamAdaptor` has no `should_fire` verb — the fire condition IS "did
    `inputs` render anything". A silent room not firing is DEC-185, and it is
    correct: nothing here retires, so there is nothing to refresh against."""
    assert await _adaptor(_Store()).inputs(key=KEY, batch=()) == ""


async def test_the_previous_account_comes_FIRST_when_there_is_one():
    """It is how the batch is meant to be read. A batch handed over with no
    prior context produces a fresh snapshot every day, which is the thing the
    chain exists to be instead of."""
    out = await _adaptor(_Store(_revision("they decide by attrition"))).inputs(
        key=KEY, batch=[_row("hi")],
    )
    assert out.index("previous_account") < out.index("<batch ")
    assert "they decide by attrition" in out


async def test_the_first_fold_of_a_room_renders_no_previous_block():
    out = await _adaptor(_Store()).inputs(key=KEY, batch=[_row("hi")])
    assert "previous_account" not in out


async def test_the_batch_carries_NO_row_ids_and_NO_timestamps():
    """The objective's load-bearing clause is «not dated observations». A model
    handed `[482] 2026-09-12 14:03` writes about the 12th; handed a name and a
    line it writes about the room. Handing over the chronicle shape and then
    asking for its opposite is the failure this rendering avoids."""
    import re

    out = await _adaptor(_Store()).inputs(
        key=KEY, batch=[_row("開車喇", ts=T0, rid=482)],
    )
    batch = out.split("<batch ")[1].split("</batch>")[0]
    body = batch.split(">", 1)[1]
    # THE ROW ID, still absent. `n` is a per-render position (1..N), never
    # `row.id` — the culture fold cites nothing back, so a real id here would be
    # a handle to a stored row for no reader.
    assert "482" not in body, "a row id reached the prompt"
    assert not re.search(r"\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2}|\bago\b", body), (
        f"a date, a clock time or an age reached the prompt: {body!r}"
    )
    # Exact bytes, so «no time» cannot rot into «a time nobody grepped for».
    # `n` and `from` are the only attributes: no `age`, because `_render_batch`
    # passes no `timestamp` accessor and `render_elements` drops what is None.
    assert body.strip() == '<m n="1" from="Ada">開車喇</m>', body


async def test_the_room_block_names_the_room_and_carries_the_operators_words():
    """Issue #161 one tier over. An account is a claim ABOUT A ROOM, and the
    prompt never said which room it was reading — the same input gap that made
    the note pass write «Bath gathering» out of a games room's 「2200」. The
    label is what the room is CALLED and the description is what the OPERATOR
    says it is; neither is derivable from the room's own messages."""
    out = await _adaptor(_Store()).inputs(key=KEY, batch=[_row("hi")])
    assert out.startswith("<room "), out[:120]
    assert 'label="Zone 42"' in out
    assert "the games channel" in out
    assert "not an instruction to you" in out


async def test_the_room_block_NEVER_renders_the_conversation_id():
    """`UNNAMED_ROOM`'s rule, R-18: a room's identifier is not its name, and a
    model asked what a room IS has no use for one."""
    out = await _adaptor(
        _Store(), label=None, description=None,
    ).inputs(key=KEY, batch=[_row("hi")])
    assert KEY not in out
    assert "unnamed room" in out


async def test_her_STANDING_in_this_room_reaches_the_fold():
    """`01-the-adaptor §1.2`. The objective asks the model where she stands
    with them while the `standing` tier holds precisely that, per room — so
    without it the model infers from raw transcript a thing another store
    states, and a guess in this tier is prose that reads like an observation."""
    store = _StandingStore({("discord", "g1"): _standing()})
    out = await _adaptor(_Store(), standing=store).inputs(
        key=KEY, batch=[_row("hi")],
    )
    assert "they ask me for routes and nothing else" in out
    assert "<standing" in out


async def test_the_standing_read_uses_the_key_the_POLICY_resolves():
    """`standing` keys `(platform, conversation_id)` and this adaptor keys
    `primary_id` (GLOSSARY.md). They are equal for every live conversation and
    that is an accident of data (#229) — read with the adaptor's own key it
    matches zero rows and renders as «this room has no standing», a real state
    nothing anywhere reports as a miss."""
    store = _StandingStore()
    await _adaptor(
        _Store(), standing=store, standing_key=("signal", "+8520000"),
    ).inputs(key=KEY, batch=[_row("hi")])
    assert store.reads == [("signal", "+8520000")]


async def test_what_the_room_has_OPEN_reaches_the_fold_at_the_WORKSPACE_key():
    """The note index is a WORKSPACE store (DEC-105), so the scope key is
    translated — `NoteCandidateAdaptor`'s own read, same verb, same
    translation."""
    notes = _Notes((_note(),))
    out = await _adaptor(_Store(), notes=notes).inputs(key=KEY, batch=[_row("hi")])
    assert notes.reads == ["friends"]
    assert "dinner on the 20th" in out


async def test_a_scope_that_names_no_policy_conversation_is_LOGGED(caplog):
    """A key that resolves to nothing renders neither block — and says so.
    Silence here is the #161 shape again: a plausible, thinner prompt that
    nothing counts."""
    import logging

    with caplog.at_level(logging.WARNING, logger="nora.language.culture"):
        out = await _adaptor(
            _Store(), standing=_StandingStore(), notes=_Notes((_note(),)),
            workspace=None, standing_key=None,
        ).inputs(key=KEY, batch=[_row("hi")])

    assert "<standing" not in out and "<notes" not in out
    assert any(
        "resolves to no policy conversation" in r.getMessage()
        for r in caplog.records
    )


async def test_the_previous_account_precedes_standing_notes_AND_the_batch():
    """`00-overview §5`: the previous account is how everything after it is
    meant to be read. A fold with no prior context writes a fresh snapshot
    every day, which is the thing the chain exists to be instead of."""
    out = await _adaptor(
        _Store(_revision("they decide by attrition")),
        standing=_StandingStore({("discord", "g1"): _standing()}),
        notes=_Notes((_note(),)),
    ).inputs(key=KEY, batch=[_row("hi")])
    assert (
        out.index("previous_account")
        < out.index("<standing")
        < out.index("<notes")
        < out.index("<batch ")
    ), out


async def test_a_quiet_room_reads_NO_store_at_all():
    """The runtime's fire condition is «did `inputs` render anything», so a
    quiet room must cost nothing — including the three reads."""
    culture, standing, notes = _Store(), _StandingStore(), _Notes((_note(),))
    assert await _adaptor(
        culture, standing=standing, notes=notes,
    ).inputs(key=KEY, batch=()) == ""
    assert standing.reads == [] and notes.reads == []


async def test_a_standing_store_that_RAISES_takes_the_fold_with_it():
    """Fail-CLOSED, unlike proactive's `_standing_lines`. A run that folded
    anyway would write an account that GUESSES at her standing, and the
    runtime would report it `ok` — the §11 tautology with a store fault behind
    it. Raising makes the run `unavailable` and HOLDS the watermark, so the
    batch is folded tomorrow instead of being consumed wrong today."""
    class _Broken:
        async def read(self, *, platform: str, conversation_id: str):
            raise RuntimeError("standing store down")

    with pytest.raises(RuntimeError, match="standing store down"):
        await _adaptor(_Store(), standing=_Broken()).inputs(  # type: ignore[arg-type]
            key=KEY, batch=[_row("hi")],
        )


async def test_a_raw_sender_id_never_reaches_the_prompt():
    """R-18/DEC-80: the raw id is an E.164 or an ACI."""
    row = replace(_row("hi"), person_name=None, sender_name=None)
    out = await _adaptor(_Store()).inputs(key=KEY, batch=[row])
    assert "u1" not in out and "unknown" in out


# --- accept: one account, or none ------------------------------------------


async def test_one_well_formed_account_is_accepted():
    got = await _adaptor(_Store()).accept(key=KEY, items=[{"body": " an account "}])
    assert got == (CultureProposal(body="an account"),)


async def test_TWO_accounts_are_refused_ENTIRELY_never_picked_between():
    """A room has one account. Picking one of two would be an arbitrary choice
    made silently, on the row that claims to say how the room works."""
    m = Metrics()
    got = await _adaptor(_Store(), m).accept(
        key=KEY, items=[{"body": "one"}, {"body": "two"}],
    )
    assert got == ()
    assert m.get("nora_culture_fold_total", outcome="too_many") == 1


async def test_an_empty_envelope_is_a_real_answer_and_writes_nothing():
    """«I looked and found nothing» leaves the previous account standing, which
    the objective says is usually right."""
    m = Metrics()
    assert await _adaptor(_Store(), m).accept(key=KEY, items=[]) == ()
    assert m.get("nora_culture_fold_total", outcome="empty") == 1


@pytest.mark.parametrize("item", [{}, {"body": 7}, {"body": "   "}, {"account": "x"}])
async def test_a_malformed_item_is_dropped_not_raised(item):
    assert await _adaptor(_Store()).accept(key=KEY, items=[item]) == ()


async def test_an_oversize_account_RAISES_so_the_batch_is_not_spent_too():
    """**The refusal must not cost the batch as well**, and returning `()`
    here did exactly that.

    Traced: no proposal -> `commit(key, ())` -> status `ok` ->
    `_dream_once` advances the watermark. The account was discarded, the
    messages it was written from were consumed, and the only record was a
    counter whose label is destroyed at every process restart — so a room whose
    fold habitually runs long wrote nothing, forever, and nothing durable said
    so. Raising makes the run `unavailable`, which is the one status that HOLDS
    the cursor, so the same batch is folded again tomorrow.

    `unavailable` and not `nothing_found`: the model DID return an account, so
    «there was nothing in this batch» is a claim this run is not entitled to
    make (R-27, and the module docstring's own line about a batch that will not
    parse)."""
    m = Metrics()
    with pytest.raises(AccountNotRecordableError):
        await _adaptor(_Store(), m).accept(
            key=KEY, items=[{"body": "x" * (BODY_MAX_CHARS + 1)}],
        )
    assert m.get("nora_culture_fold_total", outcome="oversize") == 1


async def test_the_OTHER_two_refusals_still_return_and_let_the_batch_advance():
    """Only `oversize` holds. `empty` and `too_many` are «I looked and there
    was nothing usable» — true, and the previous account stands — so holding
    the cursor for them would re-fold a batch that has already been answered,
    which is the wedge with none of the benefit."""
    adaptor = _adaptor(_Store())
    assert await adaptor.accept(key=KEY, items=[]) == ()
    assert await adaptor.accept(
        key=KEY, items=[{"body": "one"}, {"body": "two"}],
    ) == ()
    assert await adaptor.accept(key=KEY, items=[{"body": "  "}]) == ()


async def test_an_account_exactly_at_the_cap_is_accepted():
    """The boundary is inclusive — an off-by-one here silently discards the
    longest legitimate account and counts it as drift."""
    got = await _adaptor(_Store()).accept(key=KEY, items=[{"body": "x" * BODY_MAX_CHARS}])
    assert len(got) == 1


# --- commit: the chain only grows when it says something new ---------------


async def test_commit_appends_with_the_RUNTIMES_provenance():
    """`accept` returns payloads precisely so an adaptor never has to know its
    own instruction digest — the runtime read the instructions and named the
    run, so these three must arrive off the `Proposal` and not be invented."""
    store = _Store()
    written = await _adaptor(store).commit(
        key=KEY, proposals=[_proposal("a new account")], now_ms=T0,
    )
    assert written == 1
    (call,) = store.appended
    assert call["instruction_sha256"] == "a" * 64
    assert call["model"] == "flash"
    assert call["established_by"] == "culture-run-1"
    assert call["written_at"] == T0


async def test_a_BYTE_IDENTICAL_account_is_not_appended():
    """The chain's whole content is that each link differs from the one before.
    A link carrying no information makes the history longer without making it
    richer."""
    m = Metrics()
    store = _Store(_revision("the same account"))
    written = await _adaptor(store, m).commit(
        key=KEY, proposals=[_proposal("the same account")], now_ms=T0,
    )
    assert written == 0 and store.appended == []
    assert m.get("nora_culture_fold_total", outcome="unchanged") == 1


async def test_a_CHANGED_account_is_appended_over_an_existing_head():
    store = _Store(_revision("the old account"))
    assert await _adaptor(store).commit(
        key=KEY, proposals=[_proposal("the new account")], now_ms=T0,
    ) == 1
    assert store.appended[0]["body"] == "the new account"


async def test_commit_never_supplies_a_parent():
    """The Dreamer dispatches scopes as concurrent tasks, so a parent captured
    before the model call is a parent that may have moved. The store chooses it
    inside its own transaction."""
    store = _Store(_revision("old"))
    await _adaptor(store).commit(key=KEY, proposals=[_proposal("new")], now_ms=T0)
    assert "parent_revision_id" not in store.appended[0]


async def test_a_foreign_payload_RAISES_rather_than_being_skipped():
    """A payload of the wrong type means the runtime and this adaptor disagree
    about what was proposed, which is a wiring fault and not a bad answer."""
    @dataclass(frozen=True)
    class _Other:
        body: str

    bad = Proposal(
        payload=_Other(body="x"), instruction_sha256="a" * 64, model="m",
        established_by="run",
    )
    with pytest.raises(TypeError, match="CultureProposal"):
        await _adaptor(_Store()).commit(key=KEY, proposals=[bad], now_ms=T0)


# --- the vocabulary is enforced, not hoped for -----------------------------


async def test_an_outcome_outside_the_vocabulary_raises():
    """A typo in a metric label creates a NEW series rather than an error, and
    the old one silently goes flat."""
    a = _adaptor(_Store(), Metrics())
    with pytest.raises(ValueError, match="vocabulary"):
        a._inc("typo")


def test_every_declared_outcome_is_reachable():
    """A vocabulary member nothing produces is a label that renders as a series
    which is always zero."""
    assert {
        "written", "empty", "too_many", "unchanged", "oversize",
    } == CULTURE_OUTCOMES


# --- the grant: a declared tool is not a reachable one ----------------------


def test_the_adaptor_declares_the_tools_and_the_name_it_is_granted_under():
    """The grant is keyed by NAME (`AdaptorGrant`), so the adaptor's `name` and
    the constant the composition root keys `dream_grants` on must be the same
    string — two spellings is a grant that silently does not apply."""
    assert CultureAdaptor.name == CULTURE_ADAPTOR_NAME == "culture"
    assert CultureAdaptor.tools == CULTURE_TOOLS


def test_every_granted_verb_is_inside_the_dreamers_READ_ONLY_ceiling():
    """`RegistryToolSource.read_only()` is the ceiling and `build` enforces it:
    a name outside it is not built at all, so it would be a declaration nothing
    honours. Asserted against the REAL registry, because the thing under test
    is the intersection with a set the registry DERIVES from
    `NativeTool.read_only` rather than lists."""
    from nora.agent.registry import ToolRegistry

    outside = CULTURE_TOOLS - ToolRegistry().read_only_tools()
    assert outside == set(), f"granted verb(s) outside the ceiling: {outside}"


def test_the_join_the_operators_example_needs_is_actually_granted():
    """`03-no-agent-loop §7.2`: the transcript carries the intent, presence
    carries who was actually there, live state carries who was elsewhere — and
    the conclusion is a property of the JOIN. `where_been` was in no adaptor's
    grant at all before this, so the example could be written and not run."""
    assert {"where_been", "online_now"} <= CULTURE_TOOLS


def test_the_situational_verbs_and_a_second_read_of_its_own_room_stay_OUT():
    """These answer a question somebody is asking NOW; an account is about what
    a room IS. `load_transcript` is refused for a different reason — the
    adaptor is HANDED its room's batch, so re-reading it spends turns to arrive
    where it started."""
    assert CULTURE_TOOLS.isdisjoint({
        "route", "transit", "parking_status", "find_parking", "weather",
        "suggest_venue", "load_transcript",
    })


def test_the_grant_names_NO_act_verb():
    """The read-only ceiling already refuses every act verb; an empty `acts` is
    that refusal said twice rather than relied on once. The Dreamer does not
    speak, and this tier writes through its own store."""
    from nora.agent.dreamer import culture_grant

    grant = culture_grant()
    assert grant.acts == frozenset()
    assert grant.home_room is True


# --- the cap and the budget are ONE decision, not two -----------------------


def test_an_account_at_the_cap_fits_inside_the_budget_that_must_emit_it():
    """**The silent, self-perpetuating trap** (`note-candidates`'
    `AdaptorSchedule` comment, reached from the other end): an output cap that
    binds truncates the JSON, `parse_envelope` reads no envelope, the run is
    `unavailable`, the watermark is HELD, and the same batch is re-read and
    re-truncated on every fire, forever. **This is what `[llm] max_tokens`
    guards and what `BODY_MAX_CHARS` does NOT** — the character cap is read in
    `accept`, on a body the model already emitted in full, so it is a quality
    refusal and never a truncation guard. The two were one paragraph until
    2026-09-17 and that is how 1,600 came to be derived from the wrong one.

    The cap is in CHARACTERS and the budget in TOKENS; the bridge is
    `ACCOUNT_OUTPUT_TOKENS`, built on the MEASURED
    `OUTPUT_TOKENS_PER_CHAR` (1.15, against 1.094 for a realistic
    dense-Cantonese account and 1.131 for continuous Cantonese prose). The
    previous 1.0 was asserted rather than taken and was optimistic on the very
    case it named, which is the direction that truncates.

    The rule: **the loop must be able to emit a full account twice** — once for
    the tool-call arguments and any preamble the pass spends before it writes,
    once for the account. Against `DreamBudgetConfig`'s own default, because
    that is what an adaptor with no `[dreamer.budgets.culture]` block gets."""
    default_loop_budget = DreamBudgetConfig().output_tokens
    assert ACCOUNT_OUTPUT_TOKENS >= BODY_MAX_CHARS
    assert default_loop_budget >= 2 * ACCOUNT_OUTPUT_TOKENS, (
        f"an account at the cap costs ~{ACCOUNT_OUTPUT_TOKENS} output tokens "
        f"and the default loop budget is {default_loop_budget}: a fold written "
        f"to the cap truncates, and a truncated fold holds its watermark and "
        f"re-reads the same batch forever"
    )


def test_an_account_at_the_cap_fits_inside_ONE_shipped_completion():
    """The tighter of the two ceilings, and a different mechanism: `[dreamer]
    max_tokens` (0 -> `[llm] max_tokens`) is the PROVIDER's per-completion
    ceiling and binds as `MaxTokensReachedException` -> `PartialReplyError`,
    not as a `stop_reason`. Read off `config.example.toml` — the shipped
    configuration, the thing an operator copies — rather than off a field
    default, because this one has none."""
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    cfg = tomllib.loads((root / "config.example.toml").read_text())
    ceiling = cfg["dreamer"].get("max_tokens") or cfg["llm"]["max_tokens"]
    assert ceiling >= ACCOUNT_OUTPUT_TOKENS, (
        f"an account at the cap costs ~{ACCOUNT_OUTPUT_TOKENS} tokens and one "
        f"completion is capped at {ceiling}"
    )


def test_the_cap_is_a_SHORT_account_measured_against_the_objectives_example():
    """The objective asks for «one paragraph or two» and prints a worked
    example. The cap must admit the second paragraph and refuse the essay, so
    it is pinned as a band around that example rather than as a bare number —
    a cap 10.8x the operator's own example (8,000, until 2026-09-17) said
    «short account» while admitting something no reader would call one.

    **The band was 1.5x-2.5x and that was loose enough to admit a wrong
    number.** `BODY_MAX_CHARS`' own comment claimed the example was «~890
    characters» and the cap «1.8x» it; extracted the way this test extracts it,
    the example is 739 and the old 1,600 was 2.17x. Both figures passed the old
    band, so the band certified an arithmetic error as correct. 1.75x-2.05x is
    tight enough that the stated ratio has to be the true one, and an
    operator-side edit to the worked example reddens this instead of silently
    re-pointing the cap."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    objective = (root / "nora/dreamer/objectives/culture.md").read_text()
    body = objective.split('{"account": [{"body": "')[1].split('"}]}')[0]
    assert len(body) == 739, (
        f"the worked example moved to {len(body)} chars; BODY_MAX_CHARS' "
        f"comment states 739 and states the ratio against it, so both have to "
        f"be re-derived rather than re-approximated"
    )
    ratio = BODY_MAX_CHARS / len(body)
    assert 1.75 <= ratio <= 2.05, (
        f"the objective's worked account is {len(body)} chars and the cap is "
        f"{BODY_MAX_CHARS} ({ratio:.2f}x): outside 1.75x-2.05x, the cap and "
        f"the instruction are asking for different things"
    )


def test_the_character_to_token_bridge_is_the_MEASURED_one():
    """**The one number in this pair that was asserted rather than taken.**

    It was 1.0 «the CJK worst case» and nothing had encoded anything. Measured
    2026-09-17 with `tiktoken`'s `o200k_base` over the JSON envelope the
    objective requires: 1.094 tok/char for a realistic dense-Cantonese account
    (89% CJK, Latin technical terms, an emoji nickname), 1.131 for continuous
    Cantonese prose with no Latin in it, and 0.227 for the objective's own
    English worked example. So 1.0 was OPTIMISTIC on the very case it named,
    which is the direction that truncates.

    `tiktoken` is not a test dependency — pinning the constant here is what
    keeps the measurement from being quietly reverted to a rounder number, and
    `nora.domain.culture.OUTPUT_TOKENS_PER_CHAR` carries the table and the
    command that produced it."""
    assert OUTPUT_TOKENS_PER_CHAR >= 1.131, (
        "below the measured continuous-Cantonese-prose ratio: an account at "
        "the cap would cost more output tokens than ACCOUNT_OUTPUT_TOKENS says"
    )
    assert ACCOUNT_OUTPUT_TOKENS >= BODY_MAX_CHARS * OUTPUT_TOKENS_PER_CHAR


def test_the_declared_store_calls_cover_the_three_inputs_and_the_two_writes():
    """`DreamerRuntime` bounds `inputs` and `commit` at this number x
    `[dreamer] store_timeout_seconds`, so a bound derived from a stale constant
    is a hold that is too wide — and a hold that is too wide looks exactly like
    a healthy one until something wedges. The architecture gate counts the
    awaits off the AST; this states what they ARE."""
    assert STORE_CALLS_PER_TICK == 5
    assert CultureAdaptor.store_calls_per_tick == STORE_CALLS_PER_TICK
