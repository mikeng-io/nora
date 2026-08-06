"""The model-facing tools (spec C.7). Thin wrappers over injected dependencies.

Docstrings are the LLM-facing schema — they are written for a model, not a developer.
Strands derives BOTH halves of what the model sees from the callable itself
(`strands.tools.decorator.FunctionToolMetadata.extract_metadata`): the text above the
`Args:` block becomes the tool `description`, and each annotated parameter becomes one
`inputSchema` property carrying its `Args:` line as that property's description. So a
parameter that exists here is a value the model is REQUIRED to invent on every call —
which is why none of them describe a conversation any more (DEC-24): both scoped tools
resolve their conversation from the closure, so asking for it bought nothing and cost
one reply per wrong guess.

feed_honcho is deliberately NOT a tool: the handler owns the single post-reply feed
(audit fix #5).

`conversation_type` and `context_window` are injected but unread. They stay on the
builder because `ToolRegistry` passes the same set to every tool it will ever
register, not because anything here uses them. `metrics` WAS in that list — DEC-24
moved the scope counter to `ReplyGuard` — and is now read by EVERY tool, where the
counter cannot live anywhere else: Honcho answers 200-with-an-empty-string for a
peer it has never heard of, so "asked and nothing known" and "could not ask" are
indistinguishable at the transport and only the call site knows which it got. That
exact ambiguity is what hid a dead `peer_card` endpoint for the whole life of the
deployment. See `_log_call` for the other half of the same argument — the one an
operator greps rather than alerts on.

`room` and `question` are what the three room-scoped tools close over so the model
never has to supply them: `room` is this conversation's membership (see
`nora.agent.room` — scope is enforced where the answer is assembled, never at
egress), and `question` is the tagging message, which `recall` uses as its search
query. Both are values Nora already holds, so under DEC-24 neither may appear in a
schema.

`build_tool_specs` builds each tool as a plain, undecorated `ToolSpec`
(`nora.agent.ports`) closed over this tag's dependencies — no `strands` import
here at all. `build_tools` is the thin, Strands-flavoured wrapper over it
(`nora.agent.ports.adapt` per spec), kept so every existing caller (the engagement
pipeline, `tests/test_tools.py`) that wants ready-to-use Strands tool objects
still can, without knowing `ToolSpec` exists. `NATIVE_TOOL_BUDGETS` is the single
source both draw budgets from — `nora.agent.registry.ToolRegistry.budgets()`
reads the very same mapping, so the number `ReplyGuard` enforces and the number
each tool is declared to cost can never drift apart (DEC-10).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..domain.ports import Recollection
from .egress import scan_identifiers
from .ports import ToolSpec, adapt
from .room import Room, RoomView

log = logging.getLogger("nora.tools")

# Fixed offset rather than a tz database lookup: Hong Kong has observed no DST
# since 1979, and this keeps `nora.agent` free of a system-tzdata dependency
# that differs between the container and a developer machine.
_HKT = timezone(timedelta(hours=8), "HKT")

@dataclass(frozen=True)
class NativeTool:
    """What is declared about a tool once, in one place.

    `budget`, `when` and `terminal` live together because they are three
    answers to the same question — how a turn is allowed to spend itself — and
    because the failure mode this table exists to prevent is a tool being added
    to one list and forgotten in the other. `budget` feeds `ReplyGuard` through
    `ToolRegistry.budgets()` (DEC-10); `when` feeds the generated capability
    block through `ToolRegistry.capability()` (DEC-29); `terminal` feeds the
    guard's turn-ending hook through `ToolRegistry.terminal_tools()` (DEC-32).
    All three are read from here and nowhere else, so a tool that exists has a
    ceiling AND a reason to be reached for AND a stated answer to "does calling
    this end the turn", or it does not build.

    `terminal` defaults to False because almost nothing is terminal and because
    the wrong default truncates a turn rather than merely lengthening one.
    Today exactly one tool sets it: `send_reply`. The next one — `send_gif`
    (`design/proposed/15-gif-replies.md` §1), or a distinct feed step on the tag
    path — is a one-line edit here rather than an edit to `guards.py`.
    """

    budget: int
    when: str
    terminal: bool = False


# ONE line per tool, in the order the capability block renders them. Keep each
# `when` to a single clause: it is paid on every model call of every tag, and it
# is not the place to restate what the schema already says.
NATIVE_TOOLS: Mapping[str, NativeTool] = {
    # Cheap, local, and the answer changes every second, so there is no reason
    # to ration it — but a budget is still required: R-7 default-denies any
    # tool absent from this mapping, which is what stops a model inventing one.
    "now": NativeTool(2, "anything that depends on when it is — never guess the "
                         "date or time from the transcript"),
    "read_buffer": NativeTool(2, "something was said further back than the messages "
                                 "you were shown"),
    "read_peer_card": NativeTool(1, "durable memory addressed by a raw platform id. "
                                    "You are never shown one, so reach for recall "
                                    "instead and do not invent an id"),
    # The three room-scoped tools. Every budget here is chosen against ONE
    # currency: a tool call is a model round trip (~4 s on this gateway), and
    # `llm.limits.turns = 8` is the real global ceiling — these numbers only
    # decide how a turn is allowed to spend it.
    #
    # 1: the membership of a group does not change inside one turn, and `Room`
    # caches the daemon read for the tag, so a second call is guaranteed to
    # return the identical answer four seconds later. Anything above 1 buys
    # nothing and can only crowd out `send_reply`.
    "group_members": NativeTool(1, "who is in this conversation. People who have not "
                                   "spoken are still here, and people a message names "
                                   "may not be"),
    # 2: one message routinely names two people (「阿豹同峰哥去咗邊」). The call
    # itself is a dict lookup over the cached roster — free — so the budget is
    # sized by round trips, not by work, and two lookups plus a reply still
    # leaves half the turn budget. A third name is rare enough to be worth
    # answering from what she has.
    "lookup_person": NativeTool(2, "who a name or nickname in a message refers to"),
    # 2: same shape, but each call is also an HTTP request (~0.2 s measured).
    # Two covers the question this exists for — "what do I remember about
    # them" for the person who asked plus one person they asked about — and
    # caps how much durable memory one turn can pull into context, which is
    # the disclosure axis R-18 cannot help with (design/11 §1.5).
    "recall": NativeTool(2, "what you durably remember about one of them. «I remember "
                            "nothing yet» and «I could not check» are different "
                            "answers; never report the second as the first"),
    # THE ONLY TERMINAL TOOL, and — verified 2026-08-06 — **the number that
    # caps how many messages one tag may send.** Both facts are easy to miss
    # from the guard's side, so they are stated here, where the number is.
    #
    # `ReplyGuard.evaluate` checks this budget (R-7) BEFORE it checks
    # `Deliverer.attempted` (R-2): a second `send_reply` is refused by the
    # budget and never reaches the latch. So relaxing R-2 alone — the obvious
    # reading of `design/proposed/16-multi-message-replies.md` — leaves
    # multi-message SILENTLY DEAD, refused by a number nobody edited. When
    # step 5 lands, **this budget IS the message cap** (DEC-32): if the cap
    # becomes operator-tunable it must arrive here, through the registry, so
    # `ReplyGuard` still reads exactly one number. Never a second cap checked
    # beside this one — that is the drift DEC-10 exists to prevent.
    "send_reply": NativeTool(1, "the only way anything you write reaches the group. "
                                "Call it once, at the end; if you never call it, "
                                "nothing is sent",
                             terminal=True),
}

# Kept as its own name because `ReplyGuard`, `ToolRegistry.budgets()` and every
# test that reasons about R-7 read it, and because DEC-10's property is that the
# number enforced and the number declared are the same object. Derived, so it
# cannot fall out of step with the table above.
NATIVE_TOOL_BUDGETS: Mapping[str, int] = {
    name: spec.budget for name, spec in NATIVE_TOOLS.items()
}

# `read_buffer`'s clamp, and the single source both the clamp and the
# model-facing docstring below read from — `tests/test_tools.py::
# test_the_model_is_told_the_same_ceiling_the_clamp_enforces` fails if they
# drift, which matters because the docstring IS the schema the model sees.
#
# THE MODEL CHOOSES `limit`. Nothing else does — it is the one number in this
# module a model invents on every call, and the clamp is the only thing
# standing between that invented number and a third of the turn's budget.
# Round-trip cost of ONE tool result, measured 2026-08-06, and paid again on
# EVERY subsequent call in the loop because the result stays in context:
#
#   read_buffer(200) -> 6,592 tokens        recall        ->   151
#   read_buffer(40)  -> 1,368 tokens        lookup_person ->   112
#
# `read_buffer` has `budget: 2` (R-7/DEC-10), so at the old ceiling of 200 two
# calls were 13,184 tokens — a fifth of `total_tokens = 64000` on their own,
# and more than half of the 24,000 that cap used to be. 60 caps the pair at
# roughly 4,000, in the same order as the three cheap tools rather than an
# order above them. 25 is the default because the prefill already carries the
# last `store.context_window` (40) messages: a call that does not say how far
# back it wants is looking for a little more, not for the whole room.
READ_BUFFER_MAX = 60
READ_BUFFER_DEFAULT = 25


# ---- one line and one counter per tool call ---------------------------------
#
# WHY THIS EXISTS, twice in one day. `grep lookup_person data/nora.log` returned
# nothing, and that was read as "the tool was never called". The grep could not
# have found anything either way: this module emitted exactly ONE log line on
# any success path (`now`'s), and `lookup_person`, `group_members`, `recall`,
# `read_buffer`, `read_peer_card` and `send_reply` emitted none at all. Two
# confident architectural conclusions were drawn from that zero and both were
# wrong; one nearly retracted a design document (DEC-28). A silence that reads
# identically whether the code ran on every turn or never is not evidence, and
# the fix is not "grep something else" — it is that a tool says it ran.
#
# WHAT MAY GO IN THE LINE. `data/nora.log` is a file on disk and this repo
# treats an identifier as unsafe at every exit (`nora.agent.egress.
# scan_identifiers`). So: the tool's name, one status, and COUNTS. Never a
# conclusion, never a transcript body, never a phone number or an ACI, and
# never the model's own argument — `_asked_about` exists precisely because a
# model will put a phone number in a `name` parameter, and a log file is a
# worse place for one than a tool result, because nothing scans it on the way
# out.


def _log_call(tool: str, status: str, **detail: object) -> None:
    """One INFO line naming the tool and how it answered.

    `tag_id` is deliberately NOT an argument and must not become one.
    `nora.obs.logging` binds it to a `structlog.contextvars` context variable
    for the lifetime of the tag (`EngagementPipeline._run_admitted`), and
    `merge_contextvars` copies it onto every record emitted while it is bound.
    A tool closure runs downstream of that binding — Strands dispatches each
    call through `asyncio.create_task`, which copies the current context — so
    the line carries `tag_id` without any tool naming it.
    `tests/test_tool_logging.py` asserts that end-to-end through the real
    structlog pipeline rather than assuming it.

    `detail` is for counts and for facts about Nora's own host (`now`'s clock).
    Anything derived from what the group said belongs in the counter's label at
    most.
    """
    log.info(
        "tool %s status=%s%s", tool, status,
        "".join(f" {key}={value}" for key, value in detail.items()),
        extra={"tool": tool, "status": status, **detail},
    )


def _record(metrics, tool: str, status: str, **detail: object) -> None:
    """Count and log a call to a tool that had no counter before."""
    metrics.inc("nora_tool_calls_total", tool=tool, status=status)
    _log_call(tool, status, **detail)


def _record_room(metrics, tool: str, status: str, **detail: object) -> None:
    """The same, for the three room-scoped tools.

    They keep `nora_room_lookups_total{tool,status}`, which predates this and
    is already alerted on (R-22). Metric names are append-only and a second
    series counting the same calls would silently double any dashboard summing
    tool volume, so the counters stay split and only the log line is uniform.
    Paired in one function so a status can never reach one and not the other.
    """
    metrics.inc("nora_room_lookups_total", tool=tool, status=status)
    _log_call(tool, status, **detail)


def _spec(name: str, call) -> ToolSpec:
    """One `ToolSpec`, with every declared field taken from `NATIVE_TOOLS[name]`.

    Written as a helper rather than seven hand-written constructor calls
    because the hand-written form is how a field gets forgotten: `terminal`
    (DEC-32) is the third field on that table, and copying `when=`/`budget=`
    seven times to add a fourth would eventually miss one and silently give
    that tool the dataclass default. A name absent from the table
    raises `KeyError` here, which is R-7's default-deny arriving at build time
    instead of at the model's first call.
    """
    declared = NATIVE_TOOLS[name]
    return ToolSpec(
        name=name, description="", schema={}, call=call,
        when=declared.when, budget=declared.budget, terminal=declared.terminal,
    )


def _asked_about(name: str) -> dict:
    """Echo the model's own word back, unless the word is an identifier.

    Echoing helps: a turn that called `lookup_person` twice needs to know which
    answer belongs to which name. But `name` is MODEL-SUPPLIED, so a model that
    passes a phone number — because it invented one, or lifted one from
    somewhere — would get it handed straight back inside a tool result, and
    R-18 fails closed on the whole reply. That found a real hole in the first
    draft of these tools: the room-scoped tools were safe by construction on
    everything they LOOKED UP and unsafe on the one field they parroted.

    `scan_identifiers`, not `scan_reply`, and the difference is deliberate. The
    hole this closes is a phone number or an ACI arriving back inside a tool
    result — an identifier is unsafe at every exit. A MODEL NAME here is not a
    disclosure at all: the model supplied the word, so echoing it tells the
    model nothing it did not just write, and if that word reaches the reply,
    `Deliverer` blocks it there, which is where R-6/R-18's rule actually lives.
    Under `scan_reply` a lookup for a person nicknamed after a poem — or any of
    the other eight ordinary words `_MODELNAME` matches — silently lost the
    disambiguation this field exists to provide.
    """
    return {} if scan_identifiers(name) else {"asked_about": name}


def _miss(name: str, view: RoomView) -> dict:
    """The answer when a name resolves to nobody in this room.

    **`not_found` says nothing about whether that person exists.** A person in
    `identity.toml` who is not in this conversation and a person who was never
    written down at all produce this identical payload, on purpose: returning
    "they exist but are not here" would disclose membership — and the existence
    of people in other rooms — through a behaviour difference, which is the
    same leak as printing the roster, just spelled slowly. See design/12 §3.

    A DEGRADED view (the platform roster was unreadable, so the scope narrowed
    to the tagger) cannot say `not_found` honestly at all: almost every name
    would miss, and «nobody here is called that» would be a confident lie about
    a room Nora could not see. A miss in that state is `unavailable`.
    """
    if view.degraded:
        return {
            "status": "unavailable", **_asked_about(name),
            "note": "I can't check who is in this group right now, so I can't tell "
                    "you who that is. Say so rather than guessing.",
        }
    return {
        "status": "not_found", **_asked_about(name),
        "note": "Nobody in this conversation goes by that. Say you're not sure who "
                "they mean — do not guess, and do not answer as if you knew them.",
    }


def build_tool_specs(
    *,
    store,
    conversation_id: str,
    conversation_type: str,
    persons,
    honcho,
    deliverer,
    metrics,
    room: Room,
    question: str = "",
    context_window: int = 40,
) -> tuple[ToolSpec, ...]:
    # The tag's conversation, captured once. It is the ONLY thing that decides
    # which rows `read_buffer` returns — see DEC-24 for why the model is no
    # longer asked for it. Task 1's cross-group read leak was this value being
    # shadowed by a same-named model-supplied parameter; deleting the parameter
    # removes the shadow along with the shape of that bug.
    bound_conversation_id = conversation_id

    async def read_buffer(limit: int = READ_BUFFER_DEFAULT) -> list[dict]:
        """Read older messages from this conversation.

        You already have the most recent messages in front of you. Only call this if you
        need to look further back than what you were shown. Returns messages oldest-first.

        Args:
            limit: How many recent messages to fetch (max 60).
        """
        # The `60` above is deliberately a LITERAL, not an f-string
        # interpolation of READ_BUFFER_MAX: an f-string here would not be a
        # docstring at all — `__doc__` would be None and Strands would derive
        # an EMPTY model-facing description and parameter schema from it
        # (`FunctionToolMetadata.extract_metadata`), which is the whole
        # contract this module's own docstring describes. The pair is kept in
        # step by a test instead, not by the interpreter.
        # Clamp both ends. `limit` is MODEL-SUPPLIED, so this clamp is the
        # only bound on it — see `READ_BUFFER_MAX` above for the measured
        # cost of getting it wrong at the top end. At the bottom end a
        # non-positive value must never reach the store: SQLite renders
        # `LIMIT -1` as unbounded, so an unclamped negative limit would
        # dump the entire conversation into context.
        asked = READ_BUFFER_DEFAULT
        try:
            asked = max(1, min(int(limit), READ_BUFFER_MAX))
            rows = await store.recent(
                conversation_id=bound_conversation_id, limit=asked,
            )
        except Exception:
            log.exception("read_buffer failed")
            _record(metrics, "read_buffer", "unavailable")
            return []
        # `reply_to` keeps this tool structurally consistent with the prefilled
        # transcript, and that consistency is the point. If the prefill teaches
        # `[A ↳ B]` and read_buffer then hands back un-threaded rows, the older
        # messages look STRUCTURALLY different and the model reads a missing
        # arrow as "not a reply". JSON, not the arrow — a tool returns data,
        # the arrow is prompt syntax.
        #
        # Resolution happens ONLY within the rows this call already returned:
        # no extra query from inside a tool, on the reply path, ever. An
        # unresolved parent drops the key rather than reporting a null the
        # model would then have to interpret.
        def who(row) -> str:
            return "Nora" if row.is_nora else (row.sender_name or "unknown")

        by_ts = {r.ts: r for r in rows}
        out = []
        threaded = 0
        for r in rows:
            item = {"sender": who(r), "body": r.body, "ts": r.ts}
            parent = by_ts.get(r.quoted_ts) if r.quoted_ts is not None else None
            if parent is not None and parent.id != r.id:
                item["reply_to"] = who(parent)
                threaded += 1
            out.append(item)
        # `rows=0` is an ordinary `ok` — a conversation with nothing older in
        # it — so the count is the discriminator, not the status. Bodies are
        # never logged: this is the group's transcript.
        _record(metrics, "read_buffer", "ok", limit=asked, rows=len(out), threaded=threaded)
        return out

    # Reviewed for the same shadowing shape as read_buffer (Task 1): `sender_id`
    # here is also model-supplied and unscoped, hardcoded to "signal". This is
    # NOT fixed in this task — see task-1-brief.md and the phase-8 notes for the
    # judgement — but it is not a silent gap either: it reads a durable,
    # per-PERSON Honcho card (not raw messages), and any sender_id valid enough
    # to resolve already had to be a real platform id someone confirmed into
    # identity.toml, so the disclosure is "what Nora recalls about a known
    # person" rather than another group's live transcript. Still a real gap:
    # nothing here checks that sender_id belongs to *this* conversation, so a
    # model that already knows (or guesses) another person's platform id can
    # pull their peer card across conversation boundaries.
    async def read_peer_card(sender_id: str) -> list[str]:
        """Look up what you durably recall about a person.

        Only call this if the recall block you were given is empty and who the person is
        actually matters to your answer. Returns an empty list if you don't know them.

        Args:
            sender_id: The platform id of the person, as shown in the conversation.
        """
        try:
            ref = persons.resolve("signal", sender_id)
            if ref is None:
                # The id itself is never logged — it is a phone number or an
                # ACI, which is exactly what R-18 keeps off every wire.
                _record(metrics, "read_peer_card", "not_found")
                return []
            # `or []` because the docstring above promises the model a list and
            # this endpoint is documented to answer `null` (DEC-27) — without
            # it the `len()` below would raise out of a tool, which
            # `test_a_tool_never_raises_into_the_agent_loop` forbids.
            card = await honcho.peer_card(peer_id=ref.honcho_peer_id) or []
        except Exception:
            log.exception("read_peer_card failed")
            _record(metrics, "read_peer_card", "unavailable")
            return []
        _record(
            metrics, "read_peer_card", "ok" if card else "nothing_known", facts=len(card),
        )
        return card

    # ---- the three room-scoped tools -------------------------------------
    #
    # All three share `room`, and that sharing IS the scope guarantee. Each one
    # resolves inside `RoomView.people` — a set built from this conversation's
    # own membership list — so a person who is in `identity.toml` but not in
    # this room is not found-and-rejected, they are absent from the index the
    # lookup runs against. See `nora.agent.room` for why resolve-within-scope
    # and resolve-then-filter are not the same thing, and design/12 §3 for the
    # leak that settled it.
    #
    # None of the three takes a conversation argument (DEC-24) and none of them
    # accepts or returns a platform id: they speak NAMES on both sides, so
    # nothing they hand the model is something R-18 would have to block on the
    # way out — and nothing they accept could address a person Nora has not
    # already placed in this room.

    async def group_members() -> dict:
        """Who is in this conversation right now.

        Call this when somebody asks who is here, who is in the group, how many
        people are in it, or whether a particular person is around. Do NOT
        guess from the transcript: people who have not spoken recently are
        still here, and people mentioned in a message may not be.

        Returns their names. It only ever describes THIS conversation, and it
        cannot tell you about anyone outside it.
        """
        view = await room.members()
        if not view.available:
            _record_room(metrics, "group_members", "unavailable")
            return {
                "status": "unavailable",
                "note": "I can't check who is in this group right now. Say so — "
                        "do not guess from the conversation.",
            }
        _record_room(
            metrics, "group_members", "ok",
            members=len(view.people), unnamed=view.unnamed,
        )
        return {
            "status": "ok",
            "members": [p.name for p in view.people],
            # A count, never the ids: the only thing Nora holds for these
            # accounts is a phone number or an ACI, and those are what R-18
            # exists to keep off the wire.
            "members_i_have_no_name_for": view.unnamed,
        }

    async def lookup_person(name: str) -> dict:
        """Work out who a name or nickname in this conversation refers to.

        Call this when a message uses a name you do not recognise — a nickname,
        a Chinese name, a handle — and knowing who it is changes your answer.
        It only knows people in THIS conversation; if it does not find them,
        say you do not know who that is rather than inventing somebody.

        Do not call it for a name you were already told (the person who tagged
        you is named for you), and do not call it for someone who is obviously
        not in the chat — a singer, a company, a friend of a friend.

        Args:
            name: The name or nickname exactly as it appeared in the message.
        """
        view = await room.scope()
        if not view.available:
            _record_room(metrics, "lookup_person", "unavailable")
            return {
                "status": "unavailable",
                "note": "I can't check who is in this group right now, so I can't "
                        "tell you who that is. Say so rather than guessing.",
            }
        matches = view.find(name)
        # `name` is never in the line. It is the one MODEL-SUPPLIED string
        # these tools take, and `_asked_about` exists because a model does put
        # phone numbers in it.
        if not matches:
            miss = _miss(name, view)
            _record_room(metrics, "lookup_person", miss["status"], candidates=0)
            return miss
        if len(matches) > 1:
            _record_room(metrics, "lookup_person", "ambiguous", candidates=len(matches))
            return {
                "status": "ambiguous", **_asked_about(name),
                "could_be": [p.name for p in matches],
                "note": "More than one person here goes by that. Ask which one.",
            }
        person = matches[0]
        _record_room(metrics, "lookup_person", "ok", candidates=1)
        return {
            "status": "ok", **_asked_about(name), "name": person.name,
            "also_called": list(person.aliases),
            "note": "This person is in this conversation.",
        }

    async def recall(person: str) -> dict:
        """What you durably remember about someone in this conversation.

        Call this when your answer depends on knowing somebody — what they do,
        what they like, something they told you before — and you were not
        already given it. What comes back is selected against the message you
        were just tagged in, so ask about the person, not about the topic.

        Name the person the way the message did; a nickname is fine. It only
        knows people in THIS conversation.

        Three different answers, and they mean different things: `ok` is what
        you remember, `nothing_known` means you genuinely remember nothing
        about them yet — which is not the same as them being a stranger — and
        `unavailable` means you could not check at all. Never present the last
        one as "I don't know them".

        Args:
            person: The name or nickname of the person, as the message said it.
        """
        view = await room.scope()
        if not view.available:
            _record_room(metrics, "recall", "unavailable")
            return {
                "status": "unavailable",
                "note": "I can't work out who that is right now. Say you can't "
                        "check — do not say you don't know them.",
            }
        matches = view.find(person)
        if not matches:
            miss = _miss(person, view)
            _record_room(metrics, "recall", miss["status"], candidates=0)
            return miss
        if len(matches) > 1:
            _record_room(metrics, "recall", "ambiguous", candidates=len(matches))
            return {
                "status": "ambiguous", **_asked_about(person),
                "could_be": [p.name for p in matches],
                "note": "More than one person here goes by that. Ask which one.",
            }
        ref = matches[0]
        try:
            memory = await honcho.representation(
                peer_id=ref.honcho_peer_id,
                # The QUESTION is Nora's to supply, not the model's (DEC-24):
                # she has the tagging message, so asking for it would be one
                # more value to invent per call. Truncated because it is a
                # search query, not a prompt.
                search_query=(question or ref.name)[:500],
                max_conclusions=5,
            )
        except Exception:
            # `MemoryStore` is documented never to be load-bearing, so this is
            # the belt for those braces — and it must land on `unavailable`,
            # never on an empty answer.
            log.exception("recall failed for %s", ref.person_id)
            memory = Recollection(reachable=False)
        if not memory.reachable:
            metrics.inc("nora_recall_total", status="unavailable")
            _record_room(metrics, "recall", "unavailable")
            return {
                "status": "unavailable", "person": ref.name,
                "note": "My memory is not answering. Say you can't check right "
                        "now — do not say you don't remember them.",
            }
        # An IDENTIFIER is dropped HERE rather than at egress, and that is not
        # belt-and-braces: R-18 fails closed on the WHOLE reply, so one
        # conclusion carrying somebody's number would cost the entire message.
        # Honcho conclusions are distilled from what people actually said, so
        # they can and do carry exactly that.
        #
        # `scan_identifiers`, not the reply predicate. This filtered through
        # `scan_egress` until 2026-08-06, which meant every durable memory
        # naming an LLM — `claude`, `gemini`, `haiku`, `opus`, `sonnet` and
        # four more — was silently withheld from a group of software engineers
        # who talk about them constantly. What she runs on stays hers because
        # `Deliverer` refuses to SAY it, not because she is made to forget
        # what other people said.
        kept = tuple(c for c in memory.conclusions if scan_identifiers(c) is None)
        withheld = len(memory.conclusions) - len(kept)
        # COUNTS, never the conclusions themselves. A conclusion is distilled
        # from what somebody actually said about a person; `data/nora.log` is
        # not where that belongs, and `withheld` is already the signal that
        # matters — a `withheld` that keeps climbing is the DEC-30 bug's shape.
        if not kept:
            metrics.inc("nora_recall_total", status="empty")
            _record_room(metrics, "recall", "ok", conclusions=0, withheld=withheld)
            return {
                "status": "nothing_known", "person": ref.name, "withheld": withheld,
                "note": "I checked and I remember nothing about them yet. That is "
                        "not the same as not knowing who they are.",
            }
        metrics.inc("nora_recall_total", status="ok")
        _record_room(metrics, "recall", "ok", conclusions=len(kept), withheld=withheld)
        return {
            "status": "ok", "person": ref.name, "recall": list(kept),
            "withheld": withheld,
            "partial_view": view.degraded,
        }

    async def now() -> str:
        """The current date and time, in Hong Kong time.

        Call this before answering anything that depends on when it is — how
        long until something, whether a place is still open, what "tonight" or
        "yesterday" refers to, or how long ago a message was sent.

        Returns: the weekday, date, time and timezone.
        """
        moment = datetime.now(_HKT)
        # The clock reading stays in the line — it is a fact about Nora's own
        # host, not about the group, and it is the one thing that tells an
        # operator the container's timezone is what they think it is. It was
        # this module's only pre-existing log line.
        _record(metrics, "now", "ok", at=moment.isoformat(timespec="seconds"))
        return moment.strftime("%A %-d %B %Y, %H:%M (%Z)")

    async def send_reply(text: str) -> bool:
        """Send your reply to the group. Call this exactly once, at the end of your turn.

        This is the only way anything you write reaches the group. If you do not call it,
        nothing is sent. It always goes to the conversation you were tagged in — you do
        not choose where, and there is nothing to name.

        Args:
            text: Your reply, in your own voice.
        """
        sent = await deliverer.deliver(text, source="tool")
        # A LENGTH, never the reply. `Deliverer` already logs the specific
        # reason behind every refusal (R-2 latch, R-4 empty, R-6 leak, R-18
        # egress, audit failure, send failure); this line exists so that the
        # call itself is visible in the same grep as every other tool, which is
        # what was missing.
        _record(metrics, "send_reply", "ok" if sent else "refused", chars=len(text or ""))
        return sent

    return (
        _spec("now", now),
        _spec("read_buffer", read_buffer),
        _spec("read_peer_card", read_peer_card),
        _spec("group_members", group_members),
        _spec("lookup_person", lookup_person),
        _spec("recall", recall),
        _spec("send_reply", send_reply),
    )


def build_tools(
    *,
    store,
    conversation_id: str,
    conversation_type: str,
    persons,
    honcho,
    deliverer,
    metrics,
    room: Room,
    question: str = "",
    context_window: int = 40,
) -> list:
    """The Strands-ready form of `build_tool_specs` — `adapt()` each spec so
    the docstrings above become the tool's model-facing description/schema
    (empty `description`/`schema` on every `ToolSpec` here means "derive from
    the callable", see `nora.agent.ports.adapt`)."""
    specs = build_tool_specs(
        store=store, conversation_id=conversation_id, conversation_type=conversation_type,
        persons=persons, honcho=honcho, deliverer=deliverer, metrics=metrics,
        room=room, question=question, context_window=context_window,
    )
    return [adapt(spec) for spec in specs]
