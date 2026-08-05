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
moved the scope counter to `ReplyGuard` — and is read again by the three room-scoped
tools, where the counter cannot live anywhere else: Honcho answers 200-with-an-empty-
string for a peer it has never heard of, so "asked and nothing known" and "could not
ask" are indistinguishable at the transport and only the call site knows which it
got. That exact ambiguity is what hid a dead `peer_card` endpoint for the whole life
of the deployment.

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
from datetime import datetime, timedelta, timezone

from ..domain.ports import Recollection
from .egress import scan_egress
from .ports import ToolSpec, adapt
from .room import Room, RoomView

log = logging.getLogger("nora.tools")

# Fixed offset rather than a tz database lookup: Hong Kong has observed no DST
# since 1979, and this keeps `nora.agent` free of a system-tzdata dependency
# that differs between the container and a developer machine.
_HKT = timezone(timedelta(hours=8), "HKT")

NATIVE_TOOL_BUDGETS: Mapping[str, int] = {
    # Cheap, local, and the answer changes every second, so there is no reason
    # to ration it — but a budget is still required: R-7 default-denies any
    # tool absent from this mapping, which is what stops a model inventing one.
    "now": 2,
    "read_buffer": 2,
    "read_peer_card": 1,
    "send_reply": 1,
    # The three room-scoped tools. Every budget here is chosen against ONE
    # currency: a tool call is a model round trip (~4 s on this gateway), and
    # `llm.limits.turns = 8` is the real global ceiling — these numbers only
    # decide how a turn is allowed to spend it.
    #
    # 1: the membership of a group does not change inside one turn, and `Room`
    # caches the daemon read for the tag, so a second call is guaranteed to
    # return the identical answer four seconds later. Anything above 1 buys
    # nothing and can only crowd out `send_reply`.
    "group_members": 1,
    # 2: one message routinely names two people (「阿豹同峰哥去咗邊」). The call
    # itself is a dict lookup over the cached roster — free — so the budget is
    # sized by round trips, not by work, and two lookups plus a reply still
    # leaves half the turn budget. A third name is rare enough to be worth
    # answering from what she has.
    "lookup_person": 2,
    # 2: same shape, but each call is also an HTTP request (~0.2 s measured).
    # Two covers the question this exists for — "what do I remember about
    # them" for the person who asked plus one person they asked about — and
    # caps how much durable memory one turn can pull into context, which is
    # the disclosure axis R-18 cannot help with (design/11 §1.5).
    "recall": 2,
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


def _asked_about(name: str) -> dict:
    """Echo the model's own word back, unless the word is an identifier.

    Echoing helps: a turn that called `lookup_person` twice needs to know which
    answer belongs to which name. But `name` is MODEL-SUPPLIED, so a model that
    passes a phone number — because it invented one, or lifted one from
    somewhere — would get it handed straight back inside a tool result, and
    R-18 fails closed on the whole reply. That found a real hole in the first
    draft of these tools: the room-scoped tools were safe by construction on
    everything they LOOKED UP and unsafe on the one field they parroted.
    """
    return {} if scan_egress(name) else {"asked_about": name}


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
        try:
            # Clamp both ends. `limit` is MODEL-SUPPLIED, so this clamp is the
            # only bound on it — see `READ_BUFFER_MAX` above for the measured
            # cost of getting it wrong at the top end. At the bottom end a
            # non-positive value must never reach the store: SQLite renders
            # `LIMIT -1` as unbounded, so an unclamped negative limit would
            # dump the entire conversation into context.
            rows = await store.recent(
                conversation_id=bound_conversation_id,
                limit=max(1, min(int(limit), READ_BUFFER_MAX)),
            )
        except Exception:
            log.exception("read_buffer failed")
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
        for r in rows:
            item = {"sender": who(r), "body": r.body, "ts": r.ts}
            parent = by_ts.get(r.quoted_ts) if r.quoted_ts is not None else None
            if parent is not None and parent.id != r.id:
                item["reply_to"] = who(parent)
            out.append(item)
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
                return []
            return await honcho.peer_card(peer_id=ref.honcho_peer_id)
        except Exception:
            log.exception("read_peer_card failed")
            return []

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
            metrics.inc("nora_room_lookups_total", tool="group_members", status="unavailable")
            return {
                "status": "unavailable",
                "note": "I can't check who is in this group right now. Say so — "
                        "do not guess from the conversation.",
            }
        metrics.inc("nora_room_lookups_total", tool="group_members", status="ok")
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
            metrics.inc("nora_room_lookups_total", tool="lookup_person", status="unavailable")
            return {
                "status": "unavailable",
                "note": "I can't check who is in this group right now, so I can't "
                        "tell you who that is. Say so rather than guessing.",
            }
        matches = view.find(name)
        if not matches:
            miss = _miss(name, view)
            metrics.inc("nora_room_lookups_total", tool="lookup_person", status=miss["status"])
            return miss
        if len(matches) > 1:
            metrics.inc("nora_room_lookups_total", tool="lookup_person", status="ambiguous")
            return {
                "status": "ambiguous", **_asked_about(name),
                "could_be": [p.name for p in matches],
                "note": "More than one person here goes by that. Ask which one.",
            }
        person = matches[0]
        metrics.inc("nora_room_lookups_total", tool="lookup_person", status="ok")
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
            metrics.inc("nora_room_lookups_total", tool="recall", status="unavailable")
            return {
                "status": "unavailable",
                "note": "I can't work out who that is right now. Say you can't "
                        "check — do not say you don't know them.",
            }
        matches = view.find(person)
        if not matches:
            miss = _miss(person, view)
            metrics.inc("nora_room_lookups_total", tool="recall", status=miss["status"])
            return miss
        if len(matches) > 1:
            metrics.inc("nora_room_lookups_total", tool="recall", status="ambiguous")
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
            metrics.inc("nora_room_lookups_total", tool="recall", status="unavailable")
            return {
                "status": "unavailable", "person": ref.name,
                "note": "My memory is not answering. Say you can't check right "
                        "now — do not say you don't remember them.",
            }
        # Anything R-18 would refuse to let out is dropped HERE rather than at
        # egress, and that is not belt-and-braces: `scan_egress` fails closed
        # on the WHOLE reply, so one conclusion carrying somebody's number
        # would cost the entire message. Honcho conclusions are distilled from
        # what people actually said, so they can and do carry exactly that.
        kept = tuple(c for c in memory.conclusions if scan_egress(c) is None)
        withheld = len(memory.conclusions) - len(kept)
        if not kept:
            metrics.inc("nora_recall_total", status="empty")
            metrics.inc("nora_room_lookups_total", tool="recall", status="ok")
            return {
                "status": "nothing_known", "person": ref.name, "withheld": withheld,
                "note": "I checked and I remember nothing about them yet. That is "
                        "not the same as not knowing who they are.",
            }
        metrics.inc("nora_recall_total", status="ok")
        metrics.inc("nora_room_lookups_total", tool="recall", status="ok")
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
        log.info("now -> %s", moment.isoformat(timespec="seconds"))
        return moment.strftime("%A %-d %B %Y, %H:%M (%Z)")

    async def send_reply(text: str) -> bool:
        """Send your reply to the group. Call this exactly once, at the end of your turn.

        This is the only way anything you write reaches the group. If you do not call it,
        nothing is sent. It always goes to the conversation you were tagged in — you do
        not choose where, and there is nothing to name.

        Args:
            text: Your reply, in your own voice.
        """
        return await deliverer.deliver(text, source="tool")

    return (
        ToolSpec(
            name="now", description="", schema={}, call=now,
            budget=NATIVE_TOOL_BUDGETS["now"],
        ),
        ToolSpec(
            name="read_buffer", description="", schema={}, call=read_buffer,
            budget=NATIVE_TOOL_BUDGETS["read_buffer"],
        ),
        ToolSpec(
            name="read_peer_card", description="", schema={}, call=read_peer_card,
            budget=NATIVE_TOOL_BUDGETS["read_peer_card"],
        ),
        ToolSpec(
            name="group_members", description="", schema={}, call=group_members,
            budget=NATIVE_TOOL_BUDGETS["group_members"],
        ),
        ToolSpec(
            name="lookup_person", description="", schema={}, call=lookup_person,
            budget=NATIVE_TOOL_BUDGETS["lookup_person"],
        ),
        ToolSpec(
            name="recall", description="", schema={}, call=recall,
            budget=NATIVE_TOOL_BUDGETS["recall"],
        ),
        ToolSpec(
            name="send_reply", description="", schema={}, call=send_reply,
            budget=NATIVE_TOOL_BUDGETS["send_reply"],
        ),
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
