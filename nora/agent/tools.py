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
egress), and `question` is the tagging message, which `about` uses as its search
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

import json
import logging
import re
import urllib.parse
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone

from ..domain.media import HANDLE_CHARS
from ..domain.models import WeatherObservation
from ..domain.ports import Recollection
from ..domain.reminders import (
    Recurrence,
    RecurrenceRule,
    Reminder,
    ReminderCaps,
    ReminderState,
    ReminderStore,
    Series,
    UnknownRecurrenceZoneError,
    remaining_occurrences,
)
from .arithmetic import evaluate as evaluate_expression
from .egress import scan_identifiers
from .links import LinkCarry
from .ports import ToolSpec, adapt
from .room import Room, RoomView, _key, _names_of
from .whereis import Whereis, resolvable_places

log = logging.getLogger("nora.tools")

# Fixed offset rather than a tz database lookup: Hong Kong has observed no DST
# since 1979, and this keeps `nora.agent` free of a system-tzdata dependency
# that differs between the container and a developer machine.
_HKT = timezone(timedelta(hours=8), "HKT")


def utc_now() -> datetime:
    """The default clock for anything in this module that needs one.

    A NAMED function, not an inline `lambda: datetime.now(UTC)` in a signature
    and never `= datetime.now(UTC)`: ruff's `B008` rejects a call in an argument
    default, and it is right to — that form freezes the clock at import time, so
    every reminder set after the process booted would be measured against the
    moment it started. `nora.agent.registry` imports this as its own default, so
    the tool layer has ONE clock rather than two that can disagree.
    """
    return datetime.now(UTC)

# Google Maps URLs API travelmode per Routes mode — the deep link `route`
# returns on `ok` (keyless, cross-platform, documented-stable).
_MAPS_TRAVELMODE = {"TRANSIT": "transit", "DRIVE": "driving", "WALK": "walking"}

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

    `time_shiftable` is the fourth (design/14 §5.1), and it is the one field
    here that is not a boolean: `None` means the tool has no time dimension,
    `False` means its answer describes this instant and decays, `True` means it
    accepts a target time. See `nora.agent.ports.ToolSpec` for the full
    argument. It is read through `ToolRegistry.live_only_tools()` and rendered
    into the capability block, so declaring it is not bookkeeping — a tool
    marked `False` is one the model is told never to quote for a later moment.

    `plugin` is the fifth (DEC-50, the activation of DEC-48). It marks a tool
    this table gives a BUDGET but does NOT itself build or describe: the
    `skills` activation tool is registered by
    `strands.vended_plugins.skills.AgentSkills` (wired in `nora.agent.factory`),
    not by `build_tool_specs`, and it advertises itself through the plugin's own
    `<available_skills>` block, not through the generated capability block. So a
    `plugin=True` entry is present ONLY so R-7's default-deny admits the tool
    (`ReplyGuard` reads `NATIVE_TOOL_BUDGETS`); it is filtered out of everything
    the registry BUILDS or RENDERS — `ToolRegistry.capability()` and
    `tool_names()` — because a capability line naming `skills` would promise a
    tool to the offline/eval compositions that never wire the plugin (they build
    the capability block from this same table but supply their own runner). The
    budget is the one thing that MUST be shared; the description is the one thing
    that must not.
    """

    budget: int
    when: str
    terminal: bool = False
    time_shiftable: bool | None = None
    plugin: bool = False
    # `source_tier` is the sixth (E1/DEC-77). It answers "where does this tool's
    # answer COME from", and it exists because the model was reaching past a
    # closer source for the open web — a mall's hours web-searched instead of
    # asked of `place_info`, a saved «work» place answered "dunno" instead of
    # read from `saved_places`. Three ranks, closest first: "confirmed" (Nora's
    # own live/data tools — the weather feed, a bus, a route, a place, a fix),
    # "remembered" (what this household saved or told her), "web" (the open web,
    # last resort). `None` means the tool is not a fact SOURCE at all — an action
    # (`send_reply`, `remind`) or a pure function (`calculate`) — and is left out
    # of the priority nudge. Read through `ToolRegistry.web_tier_tools()` and rendered as
    # one sentence into the capability block, the same way `time_shiftable`
    # renders the clock line — so declaring it is not bookkeeping: it changes
    # what the always-on block tells the model to reach for first.
    source_tier: str | None = None


# ONE line per tool, in the order the capability block renders them. Keep each
# `when` to a single clause: it is paid on every model call of every tag, and it
# is not the place to restate what the schema already says.
#
# BUDGETS: the per-tool CALL budgets were DOUBLED 2026-08-08 (operator) so a
# research-heavy turn — surveying a few sources — does not hit the R-7 ceiling
# mid-answer. The per-tool number comments below describe the RELATIVE sizing,
# which a uniform doubling preserves (a tool sized "2, like route" is still
# twice group_members). FIVE are deliberately HELD at their original value, for
# reasons a bigger budget cannot help (review 2026-08-08): `send_reply` (3) is
# the messages-per-reply spam cap, not a lookup; `ask_memory` (1) is a ~14-19s
# dialectic call whose second invocation blows the turn's wall-clock; and
# `group_members` / `saved_places` / `skills` (1) each return a per-tag CACHED,
# provably-identical result, so a second call only crowds out `send_reply`.
NATIVE_TOOLS: Mapping[str, NativeTool] = {
    # Cheap, local, and the answer changes every second, so there is no reason
    # to ration it — but a budget is still required: R-7 default-denies any
    # tool absent from this mapping, which is what stops a model inventing one.
    #
    # `time_shiftable=False`: this is the clock, so its answer is about this
    # instant by definition and is stale for any other one. It is the second
    # live-only tool and the older of the two, which is worth stating here
    # rather than letting `transit` look like a special case.
    "now": NativeTool(4, "anything that depends on when it is — never guess the "
                         "date or time from the transcript",
                      time_shiftable=False, source_tier="confirmed"),
    # 4, and the only budget here NOT sized by what a call costs. This is the
    # cheapest tool in the set by a wide margin — a pure function over a
    # string, no network, no clock, no daemon, measured at well under a
    # millisecond including the cases it refuses — so the only currency it
    # spends is the model round trip, and `llm.limits.turns = 8` is the real
    # ceiling. It is sized by the QUESTION instead: a bill with a service
    # charge, one person who already paid, and a split is genuinely subtotal ->
    # +service -> -prepaid -> /people if she works it stepwise. A fifth
    # arithmetic step in one turn means she should have written one expression.
    #
    # `terminal=False`, and not by default — working out a total is the middle
    # of an answer, never the end of one; she still has to say it.
    # `time_shiftable` has no meaning for this tool in either direction, which
    # is worth stating rather than leaving ambiguous: unlike `now`, the answer
    # to `1485/7` is the same whenever it is asked.
    "calculate": NativeTool(8, "any arithmetic at all — never work a number out in "
                               "your head, and never state one you did not get "
                               "from here"),
    # 2, and sized the way `lookup_person` is — by model round trips, not by
    # work. One message routinely names two routes (「68X 定 K73 快啲」), and two
    # calls plus a reply still leaves half the turn. A third route in one
    # question has stopped being a question about a bus.
    #
    # **`time_shiftable=False` is the load-bearing declaration here** (design/14
    # §5.1). `rt.data.gov.hk` cannot time-travel: asked at 15:00 about a bus at
    # 19:30 it answers about 15:00, and 「仲有 2 分鐘」 quoted for a dinner four
    # hours away is confidently, checkably wrong. `route()`'s `arrive_by` is
    # what answers that question, and it is not built yet — which is exactly
    # why this field exists before it does.
    "transit": NativeTool(4, "a Hong Kong bus or feeder route somebody named by number "
                             "— never state a bus time you did not get from here, and "
                             "never use it for a bus later than now",
                          time_shiftable=False, source_tier="confirmed"),
    # 2, sized like `transit`: one message can name a route to plan for two
    # people leaving from two places, and two calls plus a reply still leave
    # half the turn. Each call is one or two Google Places lookups plus one
    # Routes call — paid, external, and the reason the budget is not higher.
    #
    # **`time_shiftable=True` is the load-bearing declaration, and `route` is
    # the FIRST tool to set it** (design/14 §5.1, DEC-40 reserved it). Google
    # Routes accepts `arrivalTime`, so unlike `transit` this tool CAN answer
    # about a departure hours from now — 「夜晚八點去到」 asked at three in the
    # afternoon. `terminal=False`: a route is the middle of an answer, not the
    # end of one; she still has to say it.
    "route": NativeTool(4, "how to travel between two places, or how long a journey "
                           "takes — the only tool that can answer about a trip at a "
                           "later time",
                        time_shiftable=True, source_tier="confirmed"),
    # 2, and HELD at 2 through the budget doubling (Kimi audit 2026-08-08): each
    # call is no longer a cheap local read — with the DEC-67 live refresh it can
    # BLOCK up to `refresh_timeout_seconds` (~8-10s) pinging the phone, so a
    # doubled 4 would be ~40s of where_is alone, blowing the 60s loop wall-clock.
    # Two people asked after («阿仔同阿女喺邊») is the real ceiling; the cost, not
    # just the question, sizes it now.
    #
    # `time_shiftable=False` (design/14 §5.1): the answer is the LATEST recorded
    # fix and decays from the moment it is read — the fix's age rides in the
    # result so the model can say «20 分鐘前», but asked where someone will be
    # LATER this tool has nothing. `terminal=False`: a position is the middle of
    # an answer, never the end of one.
    "where_is": NativeTool(2, "where somebody in this conversation is — never state "
                             "or guess a location you did not get from here",
                          time_shiftable=False, source_tier="confirmed"),
    # 2, sized like `route`: one message can ask after two spots («邊間好啲»),
    # each a single Google Places lookup. `time_shiftable=None`: a place's rating
    # has no time dimension the way `route` does — «幾好食» is the same question
    # whenever asked (a stale open_now is the one caveat, and Google returns it
    # fresh per call). `terminal=False`: a rating is the middle of an answer.
    "place_info": NativeTool(4, "a place — a shop, a mall, a restaurant, a clinic — "
                                "its rating, its price, its OPENING HOURS (幾點開/幾點"
                                "關門) and whether it's open right now; never guess a "
                                "rating or a time you did not get from here",
                             source_tier="confirmed"),
    # 1, like `group_members`: it lists a small fixed set for THIS room, and
    # calling it twice in a turn returns the same list. `time_shiftable=None`:
    # the household's saved names don't decay. Not terminal.
    "saved_places": NativeTool(1, "where you or someone here lives or works — «屋企», "
                                  "«公司», «返工», «Home», «Miles Home» — the personal "
                                  "place-names this household saved; check whenever "
                                  "someone asks about, or routes to, a place by a "
                                  "personal name rather than a public one",
                               source_tier="remembered"),
    # **`load_transcript`, not `read_buffer`, and that was MEASURED, not argued
    # (DEC-58, same harness as DEC-39).** The old name was a lie — nothing is
    # buffered; it reads durable rows off disk (GLOSSARY). With the docstring
    # held BYTE-IDENTICAL and only the identifier changed (schema name + this
    # capability line), against the real 13-tool schemas, the real assembled
    # doctrine and `tool_choice="required"` on `deepseek-v4-flash`,
    # reasoning_effort=low, first-tool selection went:
    #
    #   read_buffer 16/16  ->  load_transcript 16/16   (its own «頭先講咗啲咩»)
    #
    # and it pulled NOTHING off the other tools: «邊個喺度» stayed group_members
    # 16/16 and «阿豹係邊個» stayed lookup_person 16/16 under the new name. The
    # sibling rename `group_members -> room_members` was REJECTED on the same
    # harness — it dragged «阿豹係邊個» (a person-identity question) onto
    # room_members 7/48 where group_members took 0/48 (z≈2.75, DEC-39's own
    # threshold). A name is not documentation; it is the feature the router
    # matches on.
    "load_transcript": NativeTool(4, "something was said further back than the messages "
                                 "you were shown",
                                  source_tier="remembered"),
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
                                   "may not be",
                                source_tier="confirmed"),
    # 2: one message routinely names two people (「阿豹同峰哥去咗邊」). The call
    # itself is a dict lookup over the cached roster — free — so the budget is
    # sized by round trips, not by work, and two lookups plus a reply still
    # leaves half the turn budget. A third name is rare enough to be worth
    # answering from what she has.
    "lookup_person": NativeTool(4, "who a name or nickname in a message refers to",
                                source_tier="remembered"),
    # 2: same shape, but each call is also an HTTP request (~0.2 s measured).
    # Two covers the question this exists for — "what is this person like" for
    # the person who asked plus one person they asked about — and caps how much
    # durable memory one turn can pull into context, which is the disclosure
    # axis R-18 cannot help with (design/11 §1.5).
    #
    # **`about`, not `person_profile`, and that was MEASURED rather than argued
    # (DEC-39).** This was `recall`, which was a lie: it fetches a standing
    # profile, not memories. `person_profile` reads better here and is
    # snake_case, so `nora.doctrine.loader`'s structural gate would catch it for
    # free instead of it needing a place on that module's hand-kept
    # `tool_names` list. It was rejected anyway. With the docstring held
    # BYTE-IDENTICAL and only the identifier changed, against the real schemas,
    # the real assembled doctrine and `tool_choice="required"` on
    # `deepseek-v4-flash`, first-tool selection on a question the tool exists to
    # answer went:
    #
    #   recall            15/16      person_profile     0/16      about  16/16
    #
    # `person_profile` shares the token `person` with `lookup_person`, and
    # person-shaped routing collapsed onto that tool — including for a question
    # about the TRANSCRIPT, where `load_transcript` fell from 14/24 to 3/16. A name
    # is not only documentation; it is the feature the router matches on.
    "about": NativeTool(4, "what someone is LIKE, when the answer is not in the "
                           "conversation. «I know nothing about them yet» and «I could "
                           "not check» are different answers; never report the second "
                           "as the first",
                        source_tier="remembered"),
    # 1, not 2: `ask_memory` REASONS (a slow dialectic call, ~14-19s), so one per
    # turn is the ceiling — a second would blow the loop budget on its own. It is
    # the escalation from `about` (retrieve), reached only when a profile lookup
    # will not answer a pieced-together question.
    "ask_memory": NativeTool(1, "REASON about someone to answer a specific "
                                "question that has to be pieced together, when a "
                                "plain profile («about») is not enough — slower and "
                                "deeper; use sparingly",
                             source_tier="remembered"),
    "search_memory": NativeTool(4, "find the exact WORDS somebody said in this "
                                   "group, further back than load_transcript "
                                   "reaches — a phrase or a topic; not a profile "
                                   "(«about») and not a reasoned answer («ask_memory»)",
                                source_tier="remembered"),
    # 2, sized like the other look-it-up tools: one message can name two images
    # (a photo AND a screenshot), and two describe calls plus a reply still leave
    # the turn. The number is not what keeps two calls inside the loop budget —
    # the per-call vision timeout is (`nora.infra.vision`, DEC-56); budget and
    # timeout together, not budget alone.
    #
    # `terminal=False` (a description is the middle of an answer, she still has
    # to say what she saw) and `time_shiftable=None` (an image does not decay and
    # cannot be asked about "later" — it is the bytes it is).
    "media_fetch": NativeTool(4, "somebody asks about an image already in this "
                                 "conversation — what a photo shows, or what a "
                                 "screenshot says"),
    # ONE web verb now (DEC-73, Tavily): search returns ranked results WITH a
    # content snippet each in a single call, so there is no `web_fetch`. Budget 4,
    # sized by MODEL ROUND TRIPS — a turn may search, then refine once or twice —
    # but the doctrine tells her a couple is plenty. `time_shiftable=None` (a page
    # is not about a moment) and `terminal=False` (she still has to say what she
    # found with `send_reply`).
    "web_search": NativeTool(4, "look something up on the web when the answer isn't in "
                            "the conversation and isn't a bus, a route, the weather, a "
                            "person you know, or a shop/mall's hours or rating — you get "
                            "ranked results, each a title, a link and a content snippet",
                             source_tier="web"),
    # ONE weather verb, a flag for depth (DEC-72): current conditions + warnings
    # (typhoon/rainstorm) + today's outlook always; the 9-day forecast on request.
    # Warnings ride EVERY answer so a signal in force is never missed. Budget 2 —
    # one HKO round is usually enough; a second covers a follow-up.
    "weather": NativeTool(2, "the weather in Hong Kong — current temperature and humidity, "
                          "any warnings in force (typhoon signal, rainstorm, very hot), "
                          "today's outlook, and the multi-day forecast when asked",
                          source_tier="confirmed"),
    # The three reminder verbs (design spec §7). All 2, and the number is sized
    # by the CONVERSATION rather than by cost: none of the three leaves the host
    # or the database, so a call is one model round trip and nothing else, and
    # one message can genuinely set two reminders (「等陣提我洗衫同交租」) or set one
    # after cancelling another. Higher would buy nothing — the real bound on how
    # many reminders exist is `max_pending_per_requester`, checked inside the
    # tool (§9), which no budget can substitute for.
    #
    # **`terminal=False` on all three, and it is load-bearing.** Setting a
    # reminder is the MIDDLE of a turn — she still has to say 「好, 等陣提你」 —
    # so a terminal verb here would end the turn silently, with the row written
    # and nothing said. It would also break `ReplyGuard._terminal_budget_spent`,
    # whose AND over every terminal verb's budget currently reduces to
    # `send_reply` alone (`tests/test_guards_terminal.py` pins
    # `terminal_tools() == frozenset({"send_reply"})`).
    #
    # `time_shiftable` is `None` for all three, like `calculate`: `remind` TAKES
    # a time, which is the opposite of being asked about one — its answer does
    # not decay and there is no "same question, different moment" to shift.
    # The capability block is generated from this table (DEC-29), so the second
    # clause is the ONLY place the model is told recurrence exists at all
    # before it opens the tool. It was the missing half of the two asks DEC-85
    # came from: she answered 「我 set 唔到 recurring 每日提嘅 reminder」 about a
    # tool she had, because nothing here said she had it.
    "remind": NativeTool(2, "somebody asks you to tell them something later — you "
                            "store their own words and say them back into this "
                            "conversation when the time comes; once, or every "
                            "day/week/month/year until a date or a number of times"),
    "list_reminders": NativeTool(2, "somebody asks what you are still holding for "
                                    "this group — only this conversation's, never "
                                    "another room's"),
    "cancel_reminder": NativeTool(2, "somebody calls one off — takes the id "
                                     "list_reminders gave you, never one you made up"),
    # THE ONLY TERMINAL TOOL, and **the number that caps how many messages one
    # tag may send** (verified 2026-08-06). Both facts are easy to miss from the
    # guard's side, so they are stated here, where the number is.
    #
    # **3 — multi-message is now ACTIVE (DEC-42, activating design/16).** It
    # shipped INERT at 1, and the whole delivery path was already built to read
    # this one number as the cap: `Deliverer.exhausted` is
    # `attempts >= message_cap` (not `attempted`), `ReplyGuard._terminal_budget_spent`
    # ends the turn only when every terminal verb's budget is spent, and
    # `_before_model` relaxes `tool_choice` to `auto` after the first send so she
    # MAY stop before reaching the cap. So raising 1→3 HERE, and nowhere else, is
    # the entire activation — exactly the property DEC-10 exists for (the number a
    # tool costs lives with the tool). **3, not higher, on purpose:** multi-message
    # is MULTIPURPOSE — distinct acts addressed to different people, never one
    # thought chunked — and in a five-person group two or three addressees is the
    # real ceiling; the day that changes it is a one-line raise (operator decision,
    # 2026-08-07).
    #
    # `ReplyGuard.evaluate` checks this budget (R-7) BEFORE `Deliverer.exhausted`
    # (R-2), so the (cap+1)th `send_reply` is refused by the budget and the latch
    # is the backstop for the one case the two counters legitimately disagree (a
    # call that reached the audit write but not `record`). Never a second cap
    # checked beside this one — that is the drift DEC-10 exists to prevent.
    "send_reply": NativeTool(3, "the only way anything you write reaches the group; "
                                "send one message per distinct thing you are saying — "
                                "usually one, up to 3 only when addressing different "
                                "people",
                             terminal=True),
    # THE ACTIVATION TOOL (DEC-50, activating DEC-48). It is `plugin=True`, so
    # this entry exists for ONE reason: R-7 default-denies any tool absent from
    # `budgets()` (`guards.py`), and the `skills(skill_name)` tool is registered
    # by `AgentSkills` (`nora.agent.factory`), not by `build_tool_specs`. Without
    # a budget here `ReplyGuard` VETOES the activation call on every turn and the
    # skill can never load — the same DEC-42 inert shape multi-message shipped in.
    #
    # **budget 1, sized like `group_members`, not like `lookup_person`.** Every
    # budget here is one currency — a model round trip (~4 s), against
    # `llm.limits.turns = 8`. A second `skills(navigation)` in one turn re-injects
    # a body ALREADY in context — the exact context-cost `load_transcript`'s clamp
    # exists to bound — and cannot improve the answer, the same reason
    # `group_members` is 1 ("a second call returns the identical answer"). At 1,
    # R-7's repeat veto ("you already called skills and have that context, answer
    # from what you have") is precisely the right thing to tell a model that
    # tries. Today exactly one skill exists, so a turn never needs two; the day a
    # second domain ships and one message legitimately spans both, this is a
    # one-line raise (DEC-10's property), which is where `lookup_person`'s 2 would
    # come from — the argument VARIES then. It does not yet.
    #
    # `terminal=False`: activating a skill is the START of using its judgement —
    # she still has to route, or reply — never the end of a turn. `time_shiftable`
    # has no meaning here in either direction, like `calculate`: the same skill
    # body comes back whenever it is asked. Both are stated rather than left to
    # the defaults so the fifth field (`plugin`) is not the only one this entry
    # sets on purpose.
    "skills": NativeTool(1, "activate a skill named in <available_skills> to load its "
                            "full instructions before you plan",
                         terminal=False, time_shiftable=None, plugin=True),
}

# Kept as its own name because `ReplyGuard`, `ToolRegistry.budgets()` and every
# test that reasons about R-7 read it, and because DEC-10's property is that the
# number enforced and the number declared are the same object. Derived, so it
# cannot fall out of step with the table above.
NATIVE_TOOL_BUDGETS: Mapping[str, int] = {
    name: spec.budget for name, spec in NATIVE_TOOLS.items()
}

# `load_transcript`'s clamp, and the single source both the clamp and the
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
#   load_transcript(200) -> 6,592 tokens        recall*       ->   151
#   load_transcript(40)  -> 1,368 tokens        lookup_person ->   112
#
# `recall*` is `about` under its old name; its 151 is the OLD figure
# and is now low. That measurement was taken
# against `miles_novak`, whose whole store is 16 explicit observations, through a
# call shape that spent the entire budget on one channel (DEC-38). Against a
# corpus with derived levels in it — `probe_zzz`, 102 documents — the new shape
# returns ~1,100 characters of conclusions, which puts one `about` in the same
# order as `load_transcript(40)` rather than an order below it. Stated in characters
# because no tokenizer is available offline in this checkout; re-measure the
# token figure the next time one is, rather than scaling it by guess.
#
# `load_transcript` has `budget: 2` (R-7/DEC-10), so at the old ceiling of 200 two
# calls were 13,184 tokens — a fifth of `total_tokens = 64000` on their own,
# and more than half of the 24,000 that cap used to be. 60 caps the pair at
# roughly 4,000, in the same order as the three cheap tools rather than an
# order above them. 25 is the default because the prefill already carries the
# last `store.context_window` (40) messages: a call that does not say how far
# back it wants is looking for a little more, not for the whole room.
LOAD_TRANSCRIPT_MAX = 60
LOAD_TRANSCRIPT_DEFAULT = 25

# Honcho's dialectic reasoning levels (DialecticOptions.reasoning_level), from
# the live schema. `ask_memory` lets the model pick; anything off-list falls back
# to "low", the fast default (§4 design/24). Ordered shallow→deep.
_REASONING_LEVELS = frozenset({"minimal", "low", "medium", "high", "max"})


# ---- one line and one counter per tool call ---------------------------------
#
# WHY THIS EXISTS, twice in one day. `grep lookup_person data/nora.log` returned
# nothing, and that was read as "the tool was never called". The grep could not
# have found anything either way: this module emitted exactly ONE log line on
# any success path (`now`'s), and `lookup_person`, `group_members`, `about`,
# `load_transcript` and `send_reply` emitted none at all. Two
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


def _record_media(metrics, status: str, *, cached: bool = False, **detail: object) -> None:
    """`nora_media_fetch_total{status,cached}`, plus the uniform log line.

    Its OWN series, not `nora_tool_calls_total` — the `cached` axis is what makes
    "MiMo is never actually called" impossible to read from silence (DEC-31/56):
    a hit and a miss are the same `status=ok`, and only this label tells them
    apart. A second series counting the same calls would double any dashboard
    summing tool volume, the same reason the room tools keep their own counter.
    """
    metrics.inc("nora_media_fetch_total", status=status, cached=str(cached).lower())
    _log_call("media_fetch", status, cached=cached, **detail)


def _record_created(metrics, status: str, **detail: object) -> None:
    """`remind`'s two counters, incremented together.

    Paired in one function for the reason `_record_room` is: a status that
    reached one counter and not the other is a dashboard that disagrees with
    itself. `nora_reminders_created_total{status}` carries the SPECIFIC reason
    (that is the only visibility into a cap set too tight — a refusal is a
    sentence to a human, and sentences do not aggregate);
    `nora_tool_calls_total{tool,status}` keeps this module's uniform three-value
    vocabulary, with the specific reason in the log line, exactly as
    `remind`'s `refused` does.
    """
    metrics.inc("nora_reminders_created_total", status=status)
    if status == "created":
        _record(metrics, "remind", "ok", **detail)
    elif status == "unavailable":
        _record(metrics, "remind", "unavailable", **detail)
    else:
        _record(metrics, "remind", "refused", reason=status, **detail)


def _parse_when(raw: str) -> datetime | None:
    """The model's `when`, as an instant — or `None` when it is not one.

    THE MODEL WRITES THIS STRING, so an unparseable one is an ordinary event
    rather than an exception: a `ValueError` escaping a tool is a silent turn
    (R-5), and `Reminder(due_at="等陣")` would reach the port and raise there.

    A value with no offset is read as `_HKT`, not refused. The prefill states
    the time as prose — «Right now it is Saturday 8 August 2026, 19:00 (HKT)»
    — so a model asked for ISO-8601 will sometimes hand back `2026-08-08T21:00`,
    and `_HKT` is the zone that header itself named. Refusing would spend a
    model round trip on punctuation; GUESSING a different zone would invent an
    instant, which is why the answer is this one specific zone and not
    `astimezone()` of whatever the host happens to be set to. What must never
    happen is a naive value reaching the store — the port raises
    `NaiveDatetimeError` on one, deliberately.
    """
    try:
        parsed = datetime.fromisoformat(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=_HKT)
    return parsed


# One period of each recurrence, in days, and every one of them ROUNDS UP (31
# for a month, 366 for a year). It is the headroom `remind` grants the horizon
# check, so rounding up admits a legitimate first occurrence at the far edge of
# a period; rounding down would refuse a 29 Feb yearly reminder set on 1 March.
_PERIOD_DAYS = {
    Recurrence.DAILY: 1,
    Recurrence.WEEKLY: 7,
    Recurrence.MONTHLY: 31,
    Recurrence.YEARLY: 366,
}


@dataclass(frozen=True)
class _RepeatRefusal:
    """A `repeat`/`times`/`until` triple that cannot be honoured, as a value.

    Returned rather than raised because the MODEL writes all three, so a bad one
    is an ordinary event and an exception inside a tool is a silent turn (R-5) —
    the same argument `_parse_when` makes about `when`. It carries the metric
    status as well as the sentence, so a cap set too tight and a model that
    keeps writing 「每兩日」 are different series on the same counter.
    """

    status: str
    note: str
    # The three-value tool vocabulary (R-27). Every one of these is `refused` —
    # the model can fix its own arguments — except the unresolvable-timezone
    # case, which is a deployment fault no sentence in the room can repair.
    outcome: str = "refused"


def _parse_until(raw: str) -> datetime | None:
    """`until` as an instant — and a BARE DATE means the END of that day.

    「提到10月11號為止」 names a day, not midnight at the start of it. Read as
    00:00 the bound would exclude every occurrence on the last day the person
    actually named, so a fourteen-day countdown would silently stop after
    thirteen — the off-by-one that is hardest to see, because the reminder that
    goes missing is the one nearest the thing it is about.

    A value WITH a time is taken exactly as given (and, like `when`, read in
    `_HKT` when it carries no offset).
    """
    text = str(raw).strip()
    try:
        day = date.fromisoformat(text)
    except (TypeError, ValueError):
        return _parse_when(text)
    return datetime.combine(day, time.max).replace(tzinfo=_HKT)


def _parse_repeat(
    *, repeat: object, times: object, until: object,
    due_at: datetime, caps: ReminderCaps,
) -> RecurrenceRule | _RepeatRefusal | None:
    """The model's three recurrence arguments as one rule — or None, or a refusal.

    `None` is «this does not repeat», and it is the default, so nothing about a
    one-shot reminder passes through a single new decision here.

    EVERY SERIES IS BOUNDED, and this is where that becomes true rather than
    merely intended. `RecurrenceRule.count` is required by the type, so the only
    question left is what the number is: the model's `times` if it gave one,
    clamped to `caps.max_occurrences_per_series`; the cap itself if it did not.
    `until` narrows further and never widens — a series ends at whichever bound
    comes first — so «every day until the 11th» is fourteen rows and «every
    year» is sixty, not infinity.

    A bound with no repeat is REFUSED rather than ignored. `times=14` with
    `repeat="none"` is a model that meant to ask for a recurrence and did not;
    silently writing one reminder and reporting success is the reading that
    cannot be recovered from, because she then tells them it is set.
    """
    word = str(repeat or Recurrence.NONE.value).strip().lower()
    try:
        every = Recurrence(word)
    except ValueError:
        return _RepeatRefusal(
            "bad_repeat",
            f"I can't repeat something {word!r}. I do daily, weekly, monthly or "
            "yearly — say which of those is closest, or set separate reminders.",
        )
    if every is Recurrence.NONE:
        if times is not None or until is not None:
            return _RepeatRefusal(
                "bad_bound",
                "You gave me how long to keep repeating but not what to repeat. "
                "Set `repeat` to daily/weekly/monthly/yearly, or drop the limit "
                "if it really is a one-off.",
            )
        return None

    ends_at: datetime | None = None
    if until is not None:
        ends_at = _parse_until(str(until))
        if ends_at is None:
            return _RepeatRefusal(
                "bad_bound",
                "I couldn't read the end date. Give it as a date like 2026-10-11, "
                "or say how many times instead.",
            )
        if ends_at < due_at:
            return _RepeatRefusal(
                "bad_bound",
                "That end date is before the first one would even go off. Check "
                "which date they meant — one of the two is wrong.",
            )

    count = caps.max_occurrences_per_series
    if times is not None:
        try:
            # `str(...)` first, because the MODEL writes this: a JSON tool call
            # can land it as `14`, `"14"` or `14.0`, and `int(14.0)` silently
            # truncates while `int("14.7")` raises. One route in, one refusal.
            asked = int(str(times).strip())
        except (TypeError, ValueError):
            return _RepeatRefusal(
                "bad_bound",
                "I couldn't read how many times. Give me a plain number, or an "
                "end date instead.",
            )
        if asked < 1:
            return _RepeatRefusal(
                "bad_bound",
                "Fewer than one time is not a reminder. Say how many they meant, "
                "or set it as a one-off.",
            )
        count = min(asked, caps.max_occurrences_per_series)
    try:
        return RecurrenceRule(
            every=every, tz=caps.timezone, count=count, until=ends_at,
        )
    except UnknownRecurrenceZoneError:
        # A DEPLOYMENT fault, not a model one: `caps.timezone` comes from
        # `[reminders]`. `unavailable` is the honest status for it — she cannot
        # hold this, and nothing the person says would fix it.
        log.exception("recurrence timezone %r is not resolvable", caps.timezone)
        return _RepeatRefusal(
            "unavailable",
            "I can't set a repeating one right now — my calendar isn't set up "
            "properly. Say so plainly rather than promising.",
            outcome="unavailable",
        )


# A row whose `requester_person_id` no longer resolves. NOT the raw person_id:
# that key is operator vocabulary (`identity.toml`'s table name), and handing it
# to the model invites her to say it out loud as if it were what the group calls
# somebody. The listing still carries the row — dropping the field, or the row,
# would hide a reminder that is genuinely there and genuinely nobody's — so this
# says the one true thing instead: it exists, and she cannot name who set it.
#
# The state is real, not defensive padding: `identity.toml` loads once at startup
# and a pending row may be thirty days old, so the person it names may have left
# the file in between. The fire path already reaches the same conclusion from the
# same lookup and answers `failed_policy` (`nora/app/fire.py`, §12) — a row that
# lists like this is one that will not fire. The operator side of the question
# ("which id?") is `nora reminders list`, which prints `who = <person_id>` raw
# because an operator reads identity.toml and Nora does not.
_UNKNOWN_OWNER = "someone I can no longer name"


def _owner_name(persons, person_id: str) -> str:
    """The display name for a reminder's requester — `PersonRef.name`.

    The SAME source and the same lookup the fire turn uses
    (`FirePath.build_context`: `persons.by_person.get(row.requester_person_id)`),
    so what `list_reminders` calls a person and what she is called when the
    reminder actually goes out cannot drift apart.

    `getattr(..., {})` for `_known_names`'s reason: `persons` arrives here
    duck-typed.
    """
    ref = getattr(persons, "by_person", {}).get(person_id)
    name = getattr(ref, "name", "") if ref is not None else ""
    return name or _UNKNOWN_OWNER


def _handle_hex(raw: str) -> str:
    """The hex handle inside whatever the model passed — `a3f2c1`, `[image a3f2c1]`
    or a full sha256 all reduce to the same lowercase hex run.

    The model is shown `[image a3f2c1]` in the transcript (DEC-43) and may quote
    any of those forms back. The last whitespace token, stripped of brackets and
    reduced to its hex characters, is the handle; anything shorter than the
    rendered handle width is not one.
    """
    token = raw.strip().strip("[]").split()
    tail = token[-1] if token else ""
    return "".join(c for c in tail.lower() if c in "0123456789abcdef")


def _spec(name: str, call) -> ToolSpec:
    """One `ToolSpec`, with every declared field taken from `NATIVE_TOOLS[name]`.

    Written as a helper rather than nine hand-written constructor calls
    because the hand-written form is how a field gets forgotten: `terminal`
    (DEC-32) was the third field on that table and `time_shiftable` (DEC-40) is
    the fourth, and copying `when=`/`budget=` nine times to add either would
    eventually miss one and silently give that tool the dataclass default —
    which for `time_shiftable` means a live-only tool quietly declaring it has
    no time dimension. A name absent from the table raises `KeyError` here,
    which is R-7's default-deny arriving at build time instead of at the
    model's first call.
    """
    declared = NATIVE_TOOLS[name]
    return ToolSpec(
        name=name, description="", schema={}, call=call,
        when=declared.when, budget=declared.budget, terminal=declared.terminal,
        time_shiftable=declared.time_shiftable,
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


# ---- R-28: the outbound egress boundary for routing (design/14 §2.2) ---------
#
# A route query inherently contains TWO locations, and they LEAVE THE HOST to
# Google. R-18 guards REPLIES; this guards ARGUMENTS, which R-18 never saw. Two
# things are hard-blocked in an origin or a destination, BEFORE any HTTP call:
#
#   * an IDENTIFIER (phone / ACI / key) — `scan_identifiers`, the floor every
#     tool shares; a number has no business in a place name and none may leave.
#   * a RAW COORDINATE (a decimal lat,lng pair) — `_looks_like_coordinate`, the
#     presence-specific block (design/19 §5). A stored Life360 position must never
#     leave in a route argument; a person names a place, never types a lat/lng.
#     Added 2026-08-07 (council #4) BEFORE `whereis` is wired, so the barrier is
#     in place when a coordinate could first reach here.
#   * a PERSON'S NAME OR NICKNAME — the routing-specific block. 「陳生屋企」 must
#     never be turned into a home address and sent, and design/14 §2.2 is
#     explicit that there is no benign reading of a person in a route argument.
#     A blocked query returns a legible refusal and NEVER reaches the client —
#     revert this check and the person's name reaches Google.
#
# The name set is EVERY person Nora can name (`persons`), not only this room's
# members: 陳生 in 「由陳生屋企去我公司」 need not be in the group for the leak
# to be real. Matching is substring after the SAME normalisation `Room` uses (so
# 「阿豹」's wrappers and casefolding line up), and names shorter than two
# characters are skipped — a one-character alias would block every query, the
# same floor `nora.infra.transit.places` puts on its substring pass.
#
# This is the UPPER half of §2.2's guarantee; the lower half is that the resolver
# (`nora.infra.routes.places`) consults no human store and so cannot turn a name
# into a location even if one slipped past. Neither half is sufficient alone.


def _known_names(persons) -> frozenset[str]:
    """Every name and alias Nora can put to a person, normalised, ≥2 chars.

    Built once per tag from the whole address book, not from this room — the
    person a route must not name need not be a member of it."""
    names: set[str] = set()
    for ref in getattr(persons, "by_person", {}).values():
        names |= {key for key in _names_of(ref) if len(key) >= 2}
    return frozenset(names)


def _names_a_person(text: str, names: frozenset[str]) -> bool:
    """Whether a route argument names a known person. Substring, because a home
    address is phrased AROUND a name (「陳生屋企」), not as one."""
    key = _key(text)
    return any(name in key for name in names)


# A decimal lat,lng pair — «22.31,114.16» / «22.4106, 114.1245». BOTH numbers
# carry a decimal point, which is what tells a coordinate apart from a street
# address («12 Larkspur Rd» is integers): no legitimate place NAME is written this
# way, so a coordinate in a route argument is a leaked machine position, not
# somewhere a person typed. See `_looks_like_coordinate`.
_COORD_RE = re.compile(r"-?\d{1,3}\.\d+\s*,\s*-?\d{1,3}\.\d+")


def _looks_like_coordinate(text: str) -> bool:
    """Whether a route argument is a raw coordinate (design/19 §5, R-28).

    This is the outbound barrier for presence: a stored position (from Life360,
    once `whereis` is wired) must never leave in a tool ARGUMENT to Google. A
    person names a place — «沙田廣場», «Yoho Mall» — they do not type a decimal
    lat/lng pair, so blocking the pair costs no real query and closes the one
    exfil shape `scan_identifiers`/`_names_a_person` do not see (council #4,
    2026-08-07). Range is NOT checked: an out-of-range pair is still a leaked
    coordinate, and blocking it too is the safe direction."""
    return _COORD_RE.search(text) is not None


def _count_known_names(text: str, names: frozenset[str]) -> int:
    """How many distinct known person-names/aliases appear in `text`.

    For `search`'s COUNT-not-block rule (design/14 §2.2): the same normalised
    substring match `_names_a_person` uses, but a count rather than a boolean —
    the query is not blocked, only observed (`nora_search_alias_total`)."""
    key = _key(text)
    return sum(1 for name in names if name in key)


# The «no client wired» payloads for `web_search` — `[search]` ships inert,
# so this is the ordinary state, not a failure. `unavailable`, never a silent
# empty (R-5): a wiring gap must read as «I could not check», not «I found
# nothing». Copied at each use (`dict(...)`) so a caller cannot mutate the shared
# literal.
_SEARCH_OFF: Mapping[str, object] = {
    "status": "unavailable",
    "note": "I can't search the web right now. Say you couldn't look it up — don't "
            "answer as if you had.",
}
_WEATHER_OFF: Mapping[str, object] = {
    "status": "unavailable",
    "note": "I couldn't reach the weather service just now. Say you couldn't check "
            "— don't invent a temperature or forecast.",
}


def _weather_result(obs: WeatherObservation) -> dict[str, object]:
    """Render a `WeatherObservation` into the model-facing tool result — only the
    fields that are actually present, so an empty outlook or an absent typhoon
    note does not bloat the input that is re-sent on every subsequent turn
    (the fetch-token lesson, 2026-08-08). `temperatures` stays a list of
    (place, °C) so the model picks the station nearest what it was asked."""
    result: dict[str, object] = {
        "status": "ok",
        "as_of": obs.as_of,
        "warnings": list(obs.warnings),   # always present (may be []); lead with it
        "temperatures": [{"place": p, "c": c} for p, c in obs.temperatures],
        "note": "Temperatures are HKO stations across HK — use the one nearest the "
                "place asked about, don't list them all. Lead with any warning.",
    }
    if obs.humidity_pct is not None:
        result["humidity_pct"] = obs.humidity_pct
    for key, value in (
        ("general_situation", obs.general_situation),
        ("today", obs.today_forecast),
        ("outlook", obs.outlook),
        ("typhoon", obs.typhoon),
    ):
        if value:
            result[key] = value
    if obs.forecast_days:
        result["forecast"] = [
            {
                "date": d.date, "weekday": d.weekday, "weather": d.weather,
                "wind": d.wind, "max_c": d.max_c, "min_c": d.min_c,
                "rain": d.rain_probability,
            }
            for d in obs.forecast_days
        ]
    return result



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
    transit_source,
    route_source=None,
    vision_source=None,
    blob_store=None,
    vision_cache=None,
    search_source=None,
    weather_source=None,
    presence_source=None,
    presence_circles=(),
    location_trusted: bool = False,
    refresher=None,
    shortener=None,
    question: str = "",
    context_window: int = 40,
    ai_peer: str = "nora",
    fire_turn: bool = False,
    reminders: ReminderStore | None = None,
    reminder_caps: ReminderCaps | None = None,
    clock: Callable[[], datetime] | None = None,
    links: LinkCarry | None = None,
) -> tuple[ToolSpec, ...]:
    """Every native tool, closed over one turn's collaborators.

    `fire_turn` is this turn's PROVENANCE: True when the heartbeat's fire path
    built it (`nora.app.fire.FirePath`), False for a tag. Nothing carried that
    before, and a tool cannot otherwise tell the two apart — the synthetic
    message a fire turn is built from is a well-formed `NormalizedMessage` by
    construction (design spec §6.4), which is exactly what makes it
    indistinguishable.

    It defaults False so every existing caller is unchanged, and it is threaded
    from `ToolRegistry.build`. The consumer is `remind`, which refuses inside a
    fire turn — a reminder that can set a reminder is a loop with no bound, and
    the turn it would fire into is the one it is already in.

    `reminders`/`reminder_caps`/`clock` come from `ToolRegistry`, which owns all
    three for the life of the process. `reminders=None` is the honest
    "not configured" state (R-5) the offline and eval compositions ship in, not
    an error; the caps and the clock fall back to `ReminderCaps()` and
    `utc_now`, so a caller that never sets a reminder needs neither.
    """
    caps = reminder_caps or ReminderCaps()
    now_fn = clock or utc_now
    # DEC-89. `None` is the INERT case, not an error: a tool that offers a link
    # to a carrier nobody sends from behaves exactly as it did before the
    # feature existed. `deliver.build` mints the shared one and both production
    # callers pass it (pinned by
    # `tests/architecture/test_tools_and_sender_share_one_link_carrier.py`), so
    # the default is only ever taken by a caller with no `Deliverer` to share
    # with — the offline/eval compositions and every test that does not care.
    link_carry = links if links is not None else LinkCarry()
    # The tag's conversation, captured once. It is the ONLY thing that decides
    # which rows `load_transcript` returns — see DEC-24 for why the model is no
    # longer asked for it. Task 1's cross-group read leak was this value being
    # shadowed by a same-named model-supplied parameter; deleting the parameter
    # removes the shadow along with the shape of that bug.
    bound_conversation_id = conversation_id

    async def load_transcript(limit: int = LOAD_TRANSCRIPT_DEFAULT) -> list[dict]:
        """Read older messages from this conversation.

        You already have the most recent messages in front of you. Only call this if you
        need to look further back than what you were shown. Returns messages oldest-first.

        Args:
            limit: How many recent messages to fetch (max 60).
        """
        # The `60` above is deliberately a LITERAL, not an f-string
        # interpolation of LOAD_TRANSCRIPT_MAX: an f-string here would not be a
        # docstring at all — `__doc__` would be None and Strands would derive
        # an EMPTY model-facing description and parameter schema from it
        # (`FunctionToolMetadata.extract_metadata`), which is the whole
        # contract this module's own docstring describes. The pair is kept in
        # step by a test instead, not by the interpreter.
        # Clamp both ends. `limit` is MODEL-SUPPLIED, so this clamp is the
        # only bound on it — see `LOAD_TRANSCRIPT_MAX` above for the measured
        # cost of getting it wrong at the top end. At the bottom end a
        # non-positive value must never reach the store: under the old SQLite
        # backend `LIMIT -1` meant unbounded (the whole conversation into
        # context); Postgres rejects a negative LIMIT outright. Either way the
        # clamp, not the backend, owns the bound.
        asked = LOAD_TRANSCRIPT_DEFAULT
        try:
            asked = max(1, min(int(limit), LOAD_TRANSCRIPT_MAX))
            rows = await store.recent(
                conversation_id=bound_conversation_id, limit=asked,
            )
        except Exception:
            log.exception("load_transcript failed")
            _record(metrics, "load_transcript", "unavailable")
            return []
        # `reply_to` keeps this tool structurally consistent with the prefilled
        # transcript, and that consistency is the point. If the prefill teaches
        # `[A ↳ B]` and load_transcript then hands back un-threaded rows, the older
        # messages look STRUCTURALLY different and the model reads a missing
        # arrow as "not a reply". JSON, not the arrow — a tool returns data,
        # the arrow is prompt syntax.
        #
        # Resolution happens ONLY within the rows this call already returned:
        # no extra query from inside a tool, on the reply path, ever. An
        # unresolved parent drops the key rather than reporting a null the
        # model would then have to interpret.
        # The THIRD renderer of these rows, and DEC-80 unified only the other two
        # (both 2026-08-10 reviews). `store.recent` returns rows straight from the
        # audit table, so `person_name` is unset on every one of them unless it is
        # projected here — and then one prompt carries `group_members:
        # ["Grant Carver"]`, prefill lines reading `陳志明 Chi Ming`, and these
        # rows reading a third thing. That is the original bug, reproduced inside a
        # single context window, on the path that exists precisely for looking
        # further back than the prefill.
        #
        # Same fallback order as `prefill._label` deliberately: an unmapped account
        # keeps its profile name rather than collapsing to "unknown", so the two
        # renderers cannot disagree in EITHER direction.
        rows = persons.name_transcript(rows, platform=room.platform)

        def who(row) -> str:
            return "Nora" if row.is_nora else (
                row.person_name or row.sender_name or "unknown"
            )

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
        _record(metrics, "load_transcript", "ok", limit=asked, rows=len(out), threaded=threaded)
        return out

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

    async def about(person: str) -> dict:
        """What you know about someone in this conversation — their standing profile.

        Call this when your answer depends on knowing somebody — what they do,
        what they like, how they usually are — and you were not already given
        it. It is the only place that knows any of that. What comes back is
        conclusions built up about them over time, narrowed to the message you
        were just tagged in, so ask about the person, not about the topic.

        It holds no messages. For something that was actually said, load_transcript.

        Name the person the way the message did; a nickname is fine. It only
        knows people in THIS conversation.

        Three different answers, and they mean different things: `ok` is the
        profile, `nothing_known` means nothing has been concluded about them
        yet — which is not the same as them being a stranger — and
        `unavailable` means you could not check at all. Never present the last
        one as "I don't know them".

        Args:
            person: The name or nickname of the person, as the message said it.
        """
        view = await room.scope()
        if not view.available:
            _record_room(metrics, "about", "unavailable")
            return {
                "status": "unavailable",
                "note": "I can't work out who that is right now. Say you can't "
                        "check — do not say you don't know them.",
            }
        matches = view.find(person)
        if not matches:
            miss = _miss(person, view)
            _record_room(metrics, "about", miss["status"], candidates=0)
            return miss
        if len(matches) > 1:
            _record_room(metrics, "about", "ambiguous", candidates=len(matches))
            return {
                "status": "ambiguous", **_asked_about(person),
                "could_be": [p.name for p in matches],
                "note": "More than one person here goes by that. Ask which one.",
            }
        ref = matches[0]
        try:
            memory = await honcho.representation(
                # OBSERVER = Nora, OBSERVED = the person (design/24 §4). This is
                # what NORA has come to understand about them from messages she
                # witnessed — her own model — NOT the person's self-description.
                # The pair only exists because Nora joins every session with
                # observe_others=True (proven live 2026-08-07). Workspace-wide by
                # design: her model blends every conversation she shares with them;
                # the room roster above already gated WHO she may recall.
                peer_id=ai_peer,
                target=ref.honcho_peer_id,
                # The QUESTION is Nora's to supply, not the model's (DEC-24):
                # she has the tagging message, so asking for it would be one
                # more value to invent per call. Truncated because it is a
                # search query, not a prompt.
                search_query=(question or ref.name)[:500],
                # `max_conclusions` is DELIBERATELY not passed. It is not a row
                # limit — Honcho spends it across three independent queries
                # (semantic / most-derived / recent), and the arithmetic that
                # divides it lives with the call that sends it
                # (`HonchoClient.representation`). Restating a number here that
                # only means something there is how the two drift apart.
            )
        except Exception:
            # `MemoryStore` is documented never to be load-bearing, so this is
            # the belt for those braces — and it must land on `unavailable`,
            # never on an empty answer.
            log.exception("about failed for %s", ref.person_id)
            memory = Recollection(reachable=False)
        if not memory.reachable:
            metrics.inc("nora_recall_total", status="unavailable")
            _record_room(metrics, "about", "unavailable")
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
            _record_room(metrics, "about", "ok", conclusions=0, withheld=withheld)
            return {
                "status": "nothing_known", "person": ref.name, "withheld": withheld,
                "note": "I checked and I remember nothing about them yet. That is "
                        "not the same as not knowing who they are.",
            }
        metrics.inc("nora_recall_total", status="ok")
        _record_room(metrics, "about", "ok", conclusions=len(kept), withheld=withheld)
        return {
            "status": "ok", "person": ref.name, "profile": list(kept),
            "withheld": withheld,
            "partial_view": view.degraded,
        }

    async def ask_memory(person: str, depth: str = "low") -> dict:
        """Work out an answer about someone from what you've come to understand
        of them — a specific QUESTION, not a profile dump.

        This REASONS over everything you've gathered about the person and
        synthesises an answer, where `about` only hands back the raw notes. Reach
        for it when a profile lookup is not enough — «would he be up for hotpot?»,
        «does she usually run late?», «how does he get to work?» — a judgement
        that has to be pieced together, not just recalled.

        It is SLOW — several seconds, sometimes more — so use it sparingly, and
        only when `about` would not answer. You decide how hard to think with
        `depth`: `low` for a quick read, `high` or `max` when the question really
        needs it (and is worth the wait). Deeper is slower.

        Only knows people in THIS conversation, and answers about the message you
        were tagged in. Outcomes: `ok` with the answer; `unavailable` when the
        reasoning could not be done (unreachable, or nothing to work from) —
        never report that as "I don't know them", say you can't check right now;
        and, rarely, `nothing_known` when she worked something out but it can't be
        repeated here. There is no "she has no conclusions" outcome distinct from
        `unavailable`: the reasoner cannot tell "nothing known" from "could not
        reach" apart, so both are `unavailable`, honestly.

        Args:
            person: The name or nickname of the person, as the message said it.
            depth: How hard to reason — minimal, low, medium, high, or max.
        """
        level = depth if depth in _REASONING_LEVELS else "low"
        view = await room.scope()
        if not view.available:
            _record_room(metrics, "ask_memory", "unavailable")
            return {
                "status": "unavailable",
                "note": "I can't work out who that is right now. Say you can't "
                        "check — do not say you don't know them.",
            }
        matches = view.find(person)
        if not matches:
            miss = _miss(person, view)
            _record_room(metrics, "ask_memory", miss["status"], candidates=0)
            return miss
        if len(matches) > 1:
            _record_room(metrics, "ask_memory", "ambiguous", candidates=len(matches))
            return {
                "status": "ambiguous", **_asked_about(person),
                "could_be": [p.name for p in matches],
                "note": "More than one person here goes by that. Ask which one.",
            }
        ref = matches[0]
        try:
            # OBSERVER = Nora, OBSERVED = the person (design/24 §4): she reasons
            # from HER model of them. The QUESTION is the tagging message, hers to
            # supply (DEC-24), not a value the model invents. `level` is the
            # model's per-question depth/latency choice — Nora decides.
            answer = await honcho.chat(
                peer_id=ai_peer,
                target=ref.honcho_peer_id,
                query=(question or ref.name)[:500],
                reasoning_level=level,
            )
        except Exception:
            log.exception("ask_memory failed for %s", ref.person_id)
            answer = None
        if answer is None:
            # chat returns None for both unreachable and nothing-to-say; neither
            # is a licence to claim ignorance of the PERSON. Report unavailable.
            metrics.inc("nora_recall_total", status="unavailable")
            _record_room(metrics, "ask_memory", "unavailable")
            return {
                "status": "unavailable", "person": ref.name,
                "note": "I couldn't work that out just now. Say you can't check "
                        "right now — do not say you don't remember them.",
            }
        # R-18 fails closed on the WHOLE reply, so a reasoned answer carrying an
        # identifier would cost the entire message. Unlike `about`'s list, this is
        # one indivisible string — if it names an identifier it cannot be part-
        # kept, so it is withheld whole. `scan_identifiers`, not the reply
        # predicate (the `about` rationale: what she runs on stays hers because
        # the Deliverer refuses to SAY it, not because she is made to forget).
        if scan_identifiers(answer) is not None:
            # `status="empty"` to match `about`'s all-withheld path (nothing
            # usable surfaced), not a new label value — the recall metric keeps
            # its ok/empty/unavailable cardinality (review M5).
            metrics.inc("nora_recall_total", status="empty")
            _record_room(metrics, "ask_memory", "ok", withheld=1)
            return {
                "status": "nothing_known", "person": ref.name, "withheld": 1,
                "note": "I worked something out but can't repeat it here. Answer "
                        "from what you already know, or say you're not sure.",
            }
        metrics.inc("nora_recall_total", status="ok")
        _record_room(metrics, "ask_memory", "ok", depth=level)
        return {
            "status": "ok", "person": ref.name, "answer": answer,
            "partial_view": view.degraded,
        }

    async def search_memory(query: str) -> dict:
        """Find where something was actually SAID in this group's history — the
        exact words, further back than load_transcript reaches.

        Use it to recall a specific thing somebody said a long time ago —
        «邊個話要book枱», «我哋幾時講過去日本» — when you need the words themselves,
        not a profile (`about`) or a reasoned answer (`ask_memory`). It searches
        what people in THIS group have said and hands back the matching lines.

        Outcomes: `ok` with the lines (each with who said it); `nothing_found`
        when nothing matches; `unavailable` when it can't be checked — never
        report the last as the middle. Only ever surfaces words from people in
        this conversation, and never in a room with someone it can't place.

        Args:
            query: what to look for — a phrase, a topic, a thing that was said.
        """
        view = await room.scope()
        if not view.available:
            _record_room(metrics, "search_memory", "unavailable")
            return {
                "status": "unavailable",
                "note": "I can't work out who is here, so I can't look back through "
                        "what was said. Say you can't check — do not guess.",
            }
        # An UNVERIFIED room (someone here Nora can't place) does not get the
        # group's history surfaced into it — the same fail-closed shape whereis
        # draws (R-31), sharper here because these are raw words, not a distilled
        # model. A degraded view (roster unreadable) refuses for the same reason.
        if view.degraded or view.unnamed > 0:
            _record_room(metrics, "search_memory", "refused")
            return {
                "status": "refused",
                "note": "I can't dig up what was said here — someone in this "
                        "conversation I can't place. Say so; do not guess.",
            }
        # OBSERVED speakers Nora may surface into THIS room: the resolved people
        # here, by their peer id. A message from anyone else — Nora's own peer, or
        # a peer not in this room — is dropped.
        roster = {p.honcho_peer_id: p.name for p in view.people}
        try:
            # SESSION-scoped to THIS channel (Kimi audit 2026-08-08): a
            # workspace-wide search would surface a roster member's PRIVATE DM
            # words into this room. `bound_conversation_id` is the tag's channel,
            # captured once (DEC-24), never a model-supplied value.
            hits = await honcho.search(query=query[:500], conversation_id=bound_conversation_id)
        except Exception:
            log.exception("search_memory failed")
            hits = []
        kept: list[dict] = []
        for peer, content in hits:
            name = roster.get(peer)
            if name is None:
                continue                       # only what this room's people said
            # R-18 fails closed on the whole reply; a raw line carrying an
            # identifier is dropped here, not handed to the model to echo.
            if scan_identifiers(content) is not None:
                continue
            kept.append({"who": name, "said": content})
        if not kept:
            _record_room(metrics, "search_memory", "ok", results=0)
            return {
                "status": "nothing_found",
                "note": "I couldn't find anything like that in what's been said "
                        "here. Say so — don't invent a quote.",
            }
        _record_room(metrics, "search_memory", "ok", results=len(kept))
        return {"status": "ok", "results": kept, "partial_view": view.degraded}

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

    async def calculate(expression: str) -> dict:
        """Work out a number exactly. Use this for ALL arithmetic, however easy it looks.

        Never do a calculation in your head and never state a number you did not get
        from here. A wrong total sounds exactly like a right one and the group has no
        way to check it — 「每人 $247.50」 reads the same either way.

        Write the whole thing as one expression: numbers, brackets, and + - * / // % **.
        A service charge is `1350 * 1.1`; a split is `1485 / 7`; both together are
        `(1350 * 1.1) / 7`. No names, no functions, no units, no currency signs.

        `status` is `ok` or `refused`, and a refusal tells you what to fix — read it
        and try again rather than answering from your own arithmetic.

        On `ok` you get `result` (exact), and then at most one of:
          - `rounded_to_cents`, when the exact answer has more digits than money does.
          - `split`, when the division does not come out evenly. `each` is what
            everyone pays, `leftover` is what that still leaves owing, and
            `settles_as` is how to make it come out exactly. Say the leftover out
            loud — do not quietly drop it, and do not multiply it back yourself.

        This gets the arithmetic right. It does NOT check that you asked the right
        question: if you divide by 5 when six people ate, you get an exact answer to
        the wrong sum. Count the people first.

        Args:
            expression: The calculation, e.g. "(1350 * 1.1) / 7".
        """
        # The evaluator is pure and documented never to raise (see
        # `nora.agent.arithmetic`), so there is no try/except here and no
        # `unavailable` status: it holds none of this tag's collaborators, so
        # there is nothing that can be unreachable. A bare `except` would only
        # be able to invent a state that cannot happen.
        out = evaluate_expression(expression)
        # Same hole `_asked_about` exists for, in the one other place a
        # MODEL-SUPPLIED string is handed straight back. `+85290000101 + 0` is
        # real arithmetic — a unary plus on an integer — so a model can put a
        # phone number here exactly as it does in `lookup_person(name=...)`,
        # and an identifier inside a tool result costs the whole reply at R-18.
        # Only the ECHO is recoverable: the RESULT of that expression is
        # `85290000101`, which is a number and nothing distinguishes it from
        # arithmetic, here or at egress.
        if scan_identifiers(expression) is not None:
            out.pop("expression", None)
        # COUNTS and status, never the expression — `_log_call`'s rule, and
        # `data/nora.log` is a worse place for a model-supplied string than a
        # tool result is, because nothing scans it on the way out. The
        # expression still reaches the TRACE, in the result above, which is
        # what DEC-31 asked for: `1485/6` is checkable, `247.5` is not.
        _record(
            metrics, "calculate", out["status"],
            chars=len(expression or ""), split="split" in out,
        )
        return out

    async def transit(route: str, near: str = "") -> dict:
        """When the next buses leave on a Hong Kong bus or feeder route.

        Call this for ANY question about a bus by number — 「K73 幾時到」,
        「68X 仲有幾耐」, whether the last bus has gone. Never state a bus time
        you worked out yourself or remember from before; you have no way to
        know it and the group has no way to check it.

        It is LIVE ONLY. It answers about right now and nothing else, so never
        use it for a bus later today — if somebody is asking about a trip this
        evening, say you can only see what is running at the moment.

        You give it a route number. It works out which company runs that
        route on its own, and it does not know which stop anybody is standing
        at, so do not name a stop — unless the person themselves NAMED a
        place, in which case pass that name as `near` exactly as they said it
        (「K73 幾時到天恆」 is route "K73", near "天恆") and the times come
        back AT that stop.

        `status` is one of three and they are three DIFFERENT answers:
          - `ok` — `directions` has, for each way the route runs, where it is
            heading and the next times. With a resolved `near` these are AT
            that stop; otherwise they are from the start of the direction and
            a bus reaches a stop further along later than that. `scheduled`
            true means the time came off a timetable rather than off a bus
            anyone can see; say so if you quote it.
          - `nothing_found` — read `known_route`. True means the route is real
            and nothing is showing right now (the last bus has probably
            gone). False means there is no such route number at all — say you
            cannot find it and ask them to check, and do NOT say there are no
            buses.
          - `unavailable` — you could not check. Say that. Never report it as
            «no buses» and never as «no such route».

        If you passed `near`, read `near.status` too — `ok`, `not_on_route`
        (a real stop this route does not serve — worth saying), `not_found`,
        `ambiguous` (`near.could_be` lists candidates; ask which), or
        `unavailable` — and phrase each the way `note` says.

        Args:
            route: The route number exactly as it was said, e.g. "68X" or "K73".
            near: The stop or place name exactly as the person said it, e.g.
                "天恆". Leave it out unless they named one.
        """
        try:
            out = await transit_source.arrivals(route, near=near)
        except Exception:
            # `TransitClient.arrivals` is documented never to raise (it turns
            # every transport failure into `unavailable` itself). This is the
            # belt for those braces, and it must land on `unavailable` rather
            # than on an empty answer — `test_a_tool_never_raises_into_the_agent_loop`
            # is the rule, and R-5 is why «I could not check» may never be
            # served as «there is nothing».
            log.exception("transit failed")
            out = {"status": "unavailable",
                   "note": "I could not check the buses just now. Say so — do not "
                           "say there are no buses and do not guess a time."}
        # The same hole `_asked_about` and `calculate` exist for, in the third
        # place a MODEL-SUPPLIED string is handed straight back. `route` is
        # already narrowed to `[A-Z0-9]{1,7}` before it reaches a URL, so
        # nothing that looks like a phone number can survive to the echo — this
        # is the belt for that, and it costs one regex.
        if scan_identifiers(str(out.get("route", ""))) is not None:
            out.pop("route", None)
        # And the fourth: `near` is free text, echoed back as `near.asked` for
        # the same two-calls-one-turn disambiguation `_asked_about` buys, and
        # screened HERE because `nora.infra` sits below the scanner and cannot
        # reach it. The stop names beside it (`stop`, `could_be`) are
        # dataset-authored, but they are rendered text on their way to a
        # reply, so they go through the same regex — it costs microseconds and
        # means nothing in this result is unscanned.
        near_info = out.get("near")
        if isinstance(near_info, dict):
            for key in ("asked", "stop"):
                if scan_identifiers(str(near_info.get(key, ""))) is not None:
                    near_info.pop(key, None)
            if isinstance(near_info.get("could_be"), list):
                near_info["could_be"] = [
                    name for name in near_info["could_be"]
                    if scan_identifiers(str(name)) is None
                ]
        _record(
            metrics, "transit", out["status"],
            known_route=out.get("known_route"),
            directions=len(out.get("directions") or ()),
            **({"near": near_info["status"]}
               if isinstance(near_info, dict) and "status" in near_info else {}),
        )
        return out

    # The person-name set for R-28, built once per tag rather than per call.
    _route_person_names = _known_names(persons)

    async def route(
        origin: str, destination: str, mode: str = "TRANSIT", arrive_by: str = "",
        by: str = "", prefer: str = "", depart_after: str = "",
    ) -> dict:
        """How to get from one place to another, and how long it takes.

        Call this for ANY «how do I get from A to B», «which way is fastest»,
        «how long to get there» — by bus and rail (the default), by car, or on
        foot. Unlike the live bus times, this one CAN answer about a later
        moment: if the trip is for tonight or tomorrow, give `arrive_by` and it
        plans for that arrival instead of for now.

        Give it PLACES, the way the person named them — a mall, a building, a
        station, a restaurant («Yoho Mall», «黃大仙祠», «天水圍站»). It looks
        each place up itself; you do not give it coordinates or a stop code.

        If they name a place by a PERSONAL name — «屋企», «home», «阿媽屋企»,
        «Tyler 屋企», «公司» — call `saved_places` FIRST, match their words to
        one of the saved names, and pass that saved name here. That routes from
        the exact spot this household saved. Never guess a home or a personal
        place from earlier messages — check `saved_places`, and if nothing fits,
        ask where they mean.

        NEVER put a person where a place goes. «從陳生屋企» is not a place, and
        this tool will refuse a route that names someone rather than somewhere —
        ask for the actual place instead.

        `status` is one of four and they mean different things:
          - `ok` — `duration_seconds` is the whole trip. For transit, `legs` is
            each vehicle to board in order (`line`, `vehicle`, `departure_stop`,
            `departure_time`, `headsign`). Times are Google's schedule — say
            «about» when you quote one. `maps_url` is a Google Maps link for
            the same trip — include it in your reply when they want to open the
            map themselves («俾條 link 我»), pasted EXACTLY as given.
          - `no_route` — read the note. Either Google found no way between the
            two places by that mode, or a place could not be pinned to ONE spot
            (`origin_could_be` / `destination_could_be` list the possibilities;
            ask which they mean rather than guessing).
          - `unavailable` — you could not check. Say so; never report it as «no
            route».
          - `refused` — the query named a person, not a place. Ask for the place.

        Args:
            origin: Where the trip STARTS, as a place name the way it was said.
            destination: Where the trip ENDS, as a place name.
            mode: TRANSIT (default), DRIVE, or WALK.
            arrive_by: Optional. When they need to ARRIVE, as a full timestamp
                (e.g. "2026-08-07T20:00:00+08:00"). Only used for transit; get
                it from the clock, never invent one.
            depart_after: Optional. When they want to LEAVE («八點出發»), as a full
                timestamp. Transit only; get it from the clock, never invent one.
                Do not give both this and arrive_by — pick the one they meant.
            by: Optional, and ONLY if the person named a way to travel: "bus",
                "mtr", "light_rail", "tram", or "train". Leave it out when they
                just want the best route. Transit only.
            prefer: Optional. "less_walking" («唔想行咁遠») or "fewer_transfers"
                («唔想轉車»), only if they said so. Transit only.
        """
        # R-28, BEFORE anything leaves the host. Identifiers first (the shared
        # floor), then the routing-specific person block. Neither the offending
        # argument nor which one it was is echoed back — the refusal says what
        # to do without reflecting the model's own string at it.
        for place in (origin, destination):
            if scan_identifiers(place) is not None:
                _record(metrics, "route", "refused", reason="identifier")
                return {
                    "status": "refused",
                    "note": "That looks like an identifier, not a place. Give me a "
                            "place name — a building, a stop, a mall.",
                }
            if _looks_like_coordinate(place):
                # A stored presence position must never leave in a tool argument
                # (design/19 §5, R-28). Revert this and a Life360 coordinate
                # reaches Google once `whereis` is wired.
                _record(metrics, "route", "refused", reason="coordinate")
                return {
                    "status": "refused",
                    "note": "That's a raw coordinate, not a place I can route from. "
                            "Give me the place by name — a building, a stop, a mall.",
                }
            if _names_a_person(place, _route_person_names):
                _record(metrics, "route", "refused", reason="person")
                return {
                    "status": "refused",
                    "note": "I can't look up a route from or to a person — tell me "
                            "the actual place (a building, a mall, a stop), not who "
                            "is there.",
                }
        if route_source is None:
            # No client wired (offline/eval compositions, or a deployment with
            # no Google key). `unavailable`, never a silent empty — a wiring gap
            # must look like «I could not check», not «there is no route» (R-5).
            _record(metrics, "route", "unavailable")
            return {
                "status": "unavailable",
                "note": "I can't work out routes right now. Say you couldn't check — "
                        "do not say there is no way to get there.",
            }
        # The household saved-places layer (design/23 §4, DEC-60): built per
        # room HERE — the guard needs the room, which the process-wide client
        # must not hold — and EMPTY on any doubt, so an unauthorised or unsaved
        # name falls through to the public lookup indistinguishably. Import is
        # local so offline compositions (route_source None, returned above)
        # never touch the routes package.
        local_places = None
        if presence_source is not None and presence_circles:
            from ..infra.routes.places import Place
            saved = await resolvable_places(
                room=room, circles=presence_circles, presence=presence_source,
                location_trusted=location_trusted,
            )
            # `None` (failed read) and `()` (nothing you may resolve) both leave
            # `local_places` unset here, and that is right for THIS caller: the
            # saved-places layer is an ENRICHMENT over the public lookup, so a
            # failure degrades to the public answer rather than failing the route.
            # `saved_places` is where the distinction is model-facing.
            if saved:
                local_places = {
                    p.name.strip().casefold(): Place(
                        place_id=f"saved:{p.place_id}", name=p.name,
                        address=p.name, lat=p.lat, lng=p.lng,
                    )
                    for p in saved
                }
        try:
            out = await route_source.route(
                origin, destination, mode=mode, arrive_by=arrive_by,
                by=by, prefer=prefer, depart_after=depart_after,
                local_places=local_places,
            )
        except Exception:
            # `RouteClient.route` is documented never to raise; this is the belt
            # for those braces, landing on `unavailable` rather than an empty
            # answer (`test_a_tool_never_raises_into_the_agent_loop`, R-5).
            log.exception("route failed")
            out = {
                "status": "unavailable", "origin": origin, "destination": destination,
                "note": "I could not reach the routing service just now. Say so — do "
                        "not say there is no route and do not guess a time.",
            }
        # The echoed origin/destination are the model's own words, already past
        # the R-28 identifier screen above, so this is belt-and-braces; the
        # candidate lists are Google-authored place names rendered on their way
        # to a reply, screened for the same reason `transit` screens `near`.
        for key in ("origin", "destination"):
            if scan_identifiers(str(out.get(key, ""))) is not None:
                out.pop(key, None)
        for key in ("origin_could_be", "destination_could_be"):
            if isinstance(out.get(key), list):
                out[key] = [c for c in out[key] if scan_identifiers(str(c)) is None]
        if out.get("status") == "ok":
            # A Google Maps deep link for the same trip (Maps URLs API — keyless
            # and stable), built from the words AS ASKED, never from anything
            # resolved: a coordinate or resolved address in a link would publish
            # a saved place's pin to anyone the message is forwarded to (the
            # R-28 spirit at one more exit). The asked strings are already past
            # the identifier/person/coordinate screens above.
            out["maps_url"] = (
                "https://www.google.com/maps/dir/?api=1"
                f"&origin={urllib.parse.quote(origin)}"
                f"&destination={urllib.parse.quote(destination)}"
                f"&travelmode={_MAPS_TRAVELMODE.get(str(out.get('mode', '')), 'transit')}"
            )
            # Minted through Nora's OWN shortener when one is wired
            # ([shortener], self-hosted so no third party sees the trip). Never
            # load-bearing: any failure keeps the long URL, which was already a
            # fine answer.
            if shortener is not None:
                short = await shortener.shorten(out["maps_url"])
                if short:
                    out["maps_url"] = short
        _record(
            metrics, "route", out["status"],
            mode=out.get("mode"), legs=len(out.get("legs") or ()),
        )
        return out

    async def place_info(name: str) -> dict:
        """How good, how pricey, and whether-open a place is — from Google.

        Call this for «X 好唔好食», «邊間好啲», «幾點開門/幾點關門», «仲開唔開門», «貴唔貴» about a
        shop, a restaurant, a clinic, a mall — anything Google Maps knows. Give
        it the place the way it was named. This is the RIGHT tool for a rating:
        it returns Google's own number, not a guess and not a scraped page.

        `status` is one of four:
          - `ok` — the place, with whatever Google had: `rating` (out of 5),
            `price_level`, `open_now`, and `maps_url` (a Google Maps link to the
            place — include it in your reply so they can tap through). A place
            may have NO rating (a bus stop, a new shop) — then say you found the
            place but it has no score yet, never invent one. Quote the rating as
            Google's, and «about».
          - `ambiguous` — several places match; `could_be` lists them. Ask which.
          - `not_found` — Google knows no such place. Say so.
          - `unavailable` — you couldn't check. Say so; don't guess a rating.

        Args:
            name: The place, as it was said («泰斗», «Yoho Mall 嘅譚仔»).
        """
        # R-28 at the one egress this tool has: the name leaves the host to
        # Google Places, so an identifier must never ride it — the same screen
        # `route` puts on origin/destination. A coordinate or a person's name is
        # not a place to look up.
        if scan_identifiers(name) is not None or _looks_like_coordinate(name):
            _record(metrics, "place_info", "refused", reason="identifier")
            return {
                "status": "refused",
                "note": "That's not a place name I can look up. Give me the name of "
                        "a shop, a restaurant, a building.",
            }
        if _names_a_person(name, _route_person_names):
            _record(metrics, "place_info", "refused", reason="person")
            return {
                "status": "refused",
                "note": "I can only look up a PLACE — a shop or a restaurant — not a "
                        "person.",
            }
        if route_source is None:
            _record(metrics, "place_info", "unavailable")
            return {
                "status": "unavailable",
                "note": "I can't look places up right now. Say you couldn't check — "
                        "don't guess a rating.",
            }
        try:
            out = await route_source.place_info(name)
        except Exception:
            log.exception("place_info failed")
            out = {"status": "unavailable"}
        # Belt-and-braces on the Google-authored name/address on the way to a
        # reply, the same screen `route` gives its echoed fields.
        if scan_identifiers(str(out.get("name", ""))) is not None:
            out.pop("name", None)
        addr = str(out.get("address", ""))
        if scan_identifiers(addr) is not None or _looks_like_coordinate(addr):
            out.pop("address", None)
        if isinstance(out.get("could_be"), list):
            out["could_be"] = [c for c in out["could_be"]
                               if scan_identifiers(str(c)) is None]
        if out.get("status") == "ok":
            # A Google Maps link to the PLACE (search deep link, keyless), built
            # from the asked name — never a resolved coordinate (R-28, the same
            # rule route's maps_url follows). Minted short when the shortener is
            # wired; never load-bearing.
            maps_url = ("https://www.google.com/maps/search/?api=1&query="
                        + urllib.parse.quote(name))
            if shortener is not None:
                short = await shortener.shorten(maps_url)
                if short:
                    maps_url = short
            out["maps_url"] = maps_url
            # DEC-89: offer it to the turn's carrier so that a reply which NAMES
            # this place carries the link whether or not the model remembered to
            # quote it. Two anchors for one url, because the words she is likely
            # to write are not always the words she asked with: `name` is what
            # the group said («Soi.29»), `out["name"]` is what Google resolved
            # («Soi 29 Thai Kitchen»), and either appearing in the reply is
            # evidence she is recommending THIS place. `LinkCarry` dedupes by
            # url, so two anchors still append one line.
            #
            # `route` deliberately does NOT do this. Its `maps_url` is a link to
            # one specific trip, and its docstring already conditions on being
            # ASKED for it («俾條 link 我»); appending unasked would put a map
            # link under every «幾耐到» answer, which is noise rather than the
            # measured defect. One tool, the one that was measured dropping
            # them — widen only on another measurement.
            link_carry.offer(anchor=name, url=maps_url)
            link_carry.offer(anchor=str(out.get("name", "")), url=maps_url)
        _record(metrics, "place_info", out["status"], rating=out.get("rating"))
        return out

    async def saved_places() -> dict:
        """The names this household has saved for places, for THIS conversation.

        Call this the moment someone routes to — or asks WHERE — a place by a
        PERSONAL name rather than a public one: «屋企», «home», «我屋企», «阿媽
        屋企», «Tyler 屋企», «公司», «我邊度返工» (where do I work), «返學». Google
        doesn't know those; this household does. Asked where someone works or
        lives, this is the tool — not `about` (that's what a person is LIKE) and
        not `search_memory` (old words); a saved place is a place, not a memory.

        You get a list of `names` («Home», «Miles Home», «阿媽屋企») — pick
        the one they meant and pass THAT as the origin or destination to `route`
        (or the place to `place_info`), and the exact saved spot is used.

        `status`:
          - `ok` — `names` is the list (may be empty if nothing is saved). Match
            their words to a name; if none fits, don't force it — ask «邊度?» or
            use a public place name, but NEVER lift a place out of old messages.
          - `unavailable` — you can't see saved places here (nobody in this
            conversation shares location with you, or none is configured). Don't
            invent one; ask where they mean.

        These are just NAMES — the actual spot stays private machinery you never
        see. Use a name only for THIS conversation; don't carry it elsewhere.
        """
        if presence_source is None or not presence_circles:
            _record(metrics, "saved_places", "unavailable")
            return {
                "status": "unavailable",
                "note": "I can't see any saved places in this conversation.",
            }
        places = await resolvable_places(
            room=room, circles=presence_circles, presence=presence_source,
            location_trusted=location_trusted,
        )
        # `None` is a FAILED READ, not an empty answer (R-27). Folding it into
        # «no names» told the model `ok` with an empty list while the presence
        # store was unreachable, so Nora said 「你冇 save 過任何地方」 — a confident
        # wrong answer, with `status="ok"` in the metrics beside it.
        if places is None:
            _record(metrics, "saved_places", "unavailable")
            return {
                "status": "unavailable",
                "note": "I couldn't check the saved places just now. Say you "
                        "couldn't look — don't say there aren't any.",
            }
        # Names only — the coordinate is never model-facing (DEC-24/60). Empty
        # in a non-subset room (resolvable_places fell through), which is the
        # R-35 shape: the model simply has no household names to offer, and a
        # guarded name stays indistinguishable from an unsaved one.
        names = sorted({p.name for p in places})
        _record(metrics, "saved_places", "ok", count=len(names))
        return {"status": "ok", "names": names}

    async def where_is(person: str) -> dict:
        """Where somebody in this conversation was last seen.

        Call this when somebody asks where a person is («佢喺邊», «阿媽返咗
        屋企未»). Give it the person the way they were named — a name or
        nickname of somebody IN this conversation. It reads a location this
        household already shares with you, forcing a fresh reading first so the
        answer is where they ARE, not where they were last seen (it may pause a
        moment for that). Nothing you send leaves — only a member id, never a
        coordinate.

        `status` is one of five and they mean different things:
          - `ok` — where they were last recorded. `age_seconds` says how OLD
            that fix is — ALWAYS carry the age into your reply («大概 20 分鐘前
            喺…»): a position said without its age reads as «right now», which
            you do not know. `place` is this household's own name for the spot
            («Home») — prefer it in your reply; `address` is the street
            address. Both can appear together. If you get `lat`/`lng` instead,
            say you only have a rough position — NEVER read coordinates aloud
            and NEVER pass them to another tool. You may also get MEASURED
            movement: `driving` (their phone says they are in a vehicle),
            `moving` (their recent positions are apart), and `towards` (the
            saved place their distance has been CLOSING on). These are
            measurements, not intentions — say «好似向緊屋企方向» or «喺車度»,
            never promise where someone will end up or when.
          - `refused` — you may not say here: someone in this conversation is
            outside the location-sharing circle, or you can't confirm who is
            here. Say that in one clause; do not hint at the location.
          - `not_found` — nobody in this conversation goes by that name. Say
            you're not sure who they mean — do not guess.
          - `ambiguous` — more than one person here matches; `could_be` lists
            them. Ask which one.
          - `unavailable` — no recorded position, or you could not check. Say
            so — never guess a place, never make one up.

        Args:
            person: Who they asked about, exactly as they said it («阿豹», «Miles»).
        """
        if presence_source is None or not presence_circles:
            # Presence not wired (offline/eval compositions, or `[presence]`
            # disabled / identity.toml carrying no circle). `unavailable`, never
            # a silent empty — a wiring gap must look like «I could not check»,
            # not «I don't know where they are» (R-5), and never a crash.
            _record(metrics, "where_is", "unavailable")
            return {
                "status": "unavailable",
                "note": "I can't check where people are right now. Say you have no "
                        "way to see that — do not guess.",
            }
        # One `Whereis` per circle, first `ok` wins. With one circle (today's
        # deployment) this is exactly the seam's own behaviour. With several, a
        # non-`refused` answer from a circle the room IS a subset of outranks a
        # `refused` from one it is not — the refusal is about the ROOM/circle
        # pair, not the person — while `ok` from any qualifying circle is the
        # answer. All of this stays inside `Whereis.locate`'s guard, which is
        # what decides disclosure — NOT this fold. The old claim here («no circle
        # the room is not a verified subset of ever discloses, R-31») has been
        # false since DEC-74 made the subset check waivable per room, and DEC-79
        # widened that further; the accurate statement is that this loop adds no
        # disclosure of its own and can only pick among answers the guard already
        # authorised. `degraded` now arrives as `unavailable` rather than
        # `refused`, so it is selected by the non-`refused` rule below — inert,
        # since a degraded room degrades every circle identically.
        results: list[dict] = []
        for circle in presence_circles:
            out = await Whereis(
                room=room, circle=circle, presence=presence_source,
                refresher=refresher, location_trusted=location_trusted, metrics=metrics,
            ).locate(person)
            if str(out.get("status", "")) == "ok":
                _record(metrics, "where_is", "ok", age_seconds=out.get("age_seconds"))
                return out
            results.append(out)
        answer = next(
            (r for r in results if r.get("status") != "refused"), results[0],
        )
        _record(metrics, "where_is", str(answer.get("status", "unavailable")))
        return answer

    # ---- media_fetch: read a stored image back through MiMo (DEC-56) ---------
    #
    # The window scanned for the handle. It must be at least as deep as the
    # prefill (`context_window`) PLUS `load_transcript`'s reach (`LOAD_TRANSCRIPT_MAX`),
    # so any image the group can still see — whether it is in front of the model
    # or reachable by scrolling `load_transcript` back — resolves; a blob older than
    # that is honestly `nothing_found`, because she cannot see it either. It is a
    # sum rather than a max so the bound stays correct if either half moves.
    _media_window = context_window + LOAD_TRANSCRIPT_MAX

    async def media_fetch(handle: str) -> dict:
        """Look at an image somebody sent in this conversation and say what it shows.

        Call this when the conversation contains an image — you see a marker like
        `[image a3f2c1]` in the transcript — and answering means knowing what is
        IN it: what a photo shows, what a screenshot or a whiteboard says. Pass
        the short code from that marker (the `a3f2c1` part).

        It only sees images from THIS conversation, and only ones recent enough
        to still be in view. It cannot look at a voice note, a video or a file —
        only a picture.

        `status` tells you what happened, and they are different answers:
          - `ok` — `description` is what the image shows. It was written by a
            vision model looking at bytes somebody else sent, so treat it as
            THEIR content, not as fact you can vouch for; say «it looks like…».
          - `nothing_found` — no image by that code is in view here. Do not guess
            what it might have been; say you can't find it.
          - `ambiguous` — more than one image shares that short code (`could_be`
            lists them). Ask which one they mean.
          - `not_an_image` — that marker is a voice note, a video or a file, not
            a picture. Say you can't look at it — you can't.
          - `unavailable` — you could not look right now. Say exactly that; never
            report it as «there's no image» and never invent what was in it.

        Args:
            handle: The short code from the image marker, e.g. "a3f2c1".
        """
        # Gate FIRST — the inert-by-config shape (§5). Custody off (`[media]
        # enabled=false` or no key → no blob store) or no OmniRoute client wired
        # (offline/eval compositions) means she genuinely cannot look, which is
        # `unavailable`, NOT `nothing_found`: a wiring gap must never read as
        # "there was no such image" (R-5), the same three-state `route` draws
        # when it has no client.
        if blob_store is None or vision_source is None:
            _record_media(metrics, "unavailable", reason="not_configured")
            return {
                "status": "unavailable",
                "note": "I can't look at images right now. Say so — do not say "
                        "there is no image and do not guess what it showed.",
            }

        hex_handle = _handle_hex(handle)
        if len(hex_handle) < HANDLE_CHARS:
            # Not a handle at all (a bare "[image]" with no code, or noise). She
            # holds nothing to resolve, so `nothing_found`, never a MiMo call.
            _record_media(metrics, "nothing_found", reason="no_handle")
            return {
                "status": "nothing_found", "handle": hex_handle,
                "note": "I don't see an image with that code here. Say you can't "
                        "find it rather than guessing what it was.",
            }

        # Resolve WITHIN this conversation's own media_json (R-21, DEC-24), the
        # closure-scope `load_transcript` uses — a handle from another conversation
        # is un-resolvable here, not resolved-then-refused. Wrapped, because a
        # tool never raises into the loop (`test_a_tool_never_raises_...`).
        try:
            rows = await store.recent(
                conversation_id=bound_conversation_id, limit=_media_window,
            )
        except Exception:
            log.exception("media_fetch failed to read the transcript")
            _record_media(metrics, "unavailable", reason="store")
            return {
                "status": "unavailable", "handle": hex_handle,
                "note": "I can't look at images right now. Say so — do not say "
                        "there is no image and do not guess what it showed.",
            }

        # Every distinct blob in view whose sha256 starts with the handle. A
        # dict keyed by full sha256 dedups the same blob appearing in several
        # rows (a forwarded meme) while KEEPING two different blobs that share
        # the 6-hex prefix apart — 24 bits collide, and `media.py` says so.
        matches: dict[str, str] = {}
        for row in rows:
            if not row.media_json:
                continue
            try:
                blobs = json.loads(row.media_json)
            except (ValueError, TypeError):
                continue
            for blob in blobs if isinstance(blobs, list) else ():
                if not isinstance(blob, dict):
                    continue
                sha = str(blob.get("sha256", ""))
                if sha.startswith(hex_handle):
                    matches.setdefault(sha, str(blob.get("media_type", "")))

        if not matches:
            _record_media(metrics, "nothing_found", candidates=0)
            return {
                "status": "nothing_found", "handle": hex_handle,
                "note": "I don't see an image with that code here. Say you can't "
                        "find it rather than guessing what it was.",
            }
        if len(matches) > 1:
            # NEVER pick the first — two in-scope blobs sharing the prefix is a
            # question, not a default (§1). List the full 6-hex handles so she
            # can ask which.
            _record_media(metrics, "ambiguous", candidates=len(matches))
            return {
                "status": "ambiguous", "handle": hex_handle,
                "could_be": sorted(sha[:HANDLE_CHARS] for sha in matches),
                "note": "More than one image here has that code. Ask which one "
                        "they mean.",
            }

        sha256, media_type = next(iter(matches.items()))
        # Non-image blobs are turned away HERE, before any MiMo call — sending
        # audio bytes as an `image_url` would be an OmniRoute error surfaced as a
        # silent `unavailable`, and honest is «that's a voice note, not a
        # picture».
        if not media_type.startswith("image/"):
            _record_media(metrics, "not_an_image")
            return {
                "status": "not_an_image", "handle": sha256[:HANDLE_CHARS],
                "note": "That's not a picture — it's a voice note, a video or a "
                        "file. Say you can't look at it.",
            }

        # Cache by content hash BEFORE the blob is even decrypted: the sha256 is
        # the content, so a stored description is valid forever for it. A hit
        # makes no MiMo call and no `store.get` (§3). `scan_identifiers` still
        # runs below, on the cached text, so a hit can never bypass the screen.
        cached_description = vision_cache.get(sha256) if vision_cache is not None else None
        if cached_description is not None:
            return _serve_media(sha256, cached_description, cached=True)

        # Decrypt the blob Nora already holds. `store.get` returns None for an
        # ABSENT blob and RAISES `InvalidTag`/`ValueError` for a present-but-
        # corrupt or wrong-key one (the day `MEDIA_ENCRYPTION_KEY` rotates) —
        # BOTH are wrapped into `unavailable` (§4), and neither may reach the
        # loop as a raise.
        try:
            data = await blob_store.get(key=sha256)
        except Exception:
            log.exception("media_fetch could not open blob %s", sha256[:HANDLE_CHARS])
            data = None
        if not data:
            _record_media(metrics, "unavailable", reason="blob")
            return {
                "status": "unavailable", "handle": sha256[:HANDLE_CHARS],
                "note": "I can't open that image right now. Say so — do not say "
                        "there is no image and do not guess what it showed.",
            }

        try:
            result = await vision_source.describe(data=data, media_type=media_type)
        except Exception:
            # `VisionClient.describe` is documented never to raise (it turns
            # every failure into an unavailable `VisionResult` itself). This is
            # the belt for those braces — `test_a_tool_never_raises_into_the_agent_loop`
            # — landing on `unavailable`, never on an invented description.
            log.exception("media_fetch vision call raised")
            result = None
        if result is None or not getattr(result, "available", False):
            _record_media(metrics, "unavailable", reason=getattr(result, "reason", "") or "vision")
            return {
                "status": "unavailable", "handle": sha256[:HANDLE_CHARS],
                "note": "I couldn't get a look at that image just now. Say so — do "
                        "not say there is no image and do not describe it anyway.",
            }
        description = result.description
        if vision_cache is not None:
            # Store the RAW description; the screen runs on every serve, not here.
            vision_cache.put(sha256, description)
        return _serve_media(sha256, description, cached=False)

    def _serve_media(sha256: str, description: str, *, cached: bool) -> dict:
        """The `ok` path, with the untrusted-egress screen run on EVERY serve.

        The description is text a vision model wrote about bytes a group member
        sent — the trust class of a fetched web page (design/14 §2.1). It runs
        through `scan_identifiers` here, on every serve INCLUDING a cache hit, so
        the screen can never be bypassed by an earlier describe having populated
        the cache.

        **Honest about what the screen does NOT catch.** `scan_identifiers`
        matches an ACI/UUID, a key, and a `+`-prefixed E.164 — it deliberately
        does NOT match a bare local number (`9000 0101`), because blocking
        free-standing digit runs would mute her in ordinary chat (R-18's standing
        blind spot). So a photo of a business card whose number MiMo transcribes
        as `9000 0101` PASSES this screen. That is a residual risk EQUAL to
        R-18's, not a solved case — stated here rather than papered over.
        """
        if scan_identifiers(description) is not None:
            # An identifier the model transcribed off the image — dropped with a
            # note, the shape `about` uses for a conclusion that carries one. The
            # status stays `ok` (she DID look), but the words never leave.
            _record_media(metrics, "ok", cached=cached, withheld=True)
            return {
                "status": "ok", "handle": sha256[:HANDLE_CHARS], "withheld": True,
                "note": "I looked, and it has what looks like a phone number or an "
                        "ID in it, so I won't repeat what it said. Describe it only "
                        "in general terms, without the contact details.",
            }
        _record_media(metrics, "ok", cached=cached, chars=len(description))
        return {
            "status": "ok", "handle": sha256[:HANDLE_CHARS],
            "description": description,
        }

    # ---- web_search (DEC-73, Tavily) -------------------------------------
    #
    # ONE verb now: Tavily returns ranked results WITH a content snippet each in a
    # single call, so `web_fetch` and the per-tag `allowed_urls` fence are gone.
    # That fence existed because the old `fetch` handed a self-hosted Firecrawl a
    # MODEL-CHOSEN URL against Nora's own network (the SSRF surface); Tavily
    # searches from ITS servers and is never handed a URL, so there is nothing to
    # fence. What survives is the query EGRESS screen below.
    #
    # The person-name set for the search alias COUNT (design/14 §2.2), built once
    # per tag from the whole address book — the same set R-28 blocks routing on,
    # reused here to count (not block) an alias in a query.
    _search_person_names = _known_names(persons)

    async def web_search(query: str) -> dict:
        """Look something up on the web — ranked results, each a title, a link and a snippet.

        Call this when the answer is not in the conversation and is not something
        another tool already covers — not a bus (`transit`), not a route
        (`route`), not the weather (`weather`), not a person you know (`about`),
        and not a place you can look up: a shop or mall's hours, rating or whether
        it's open is `place_info`, not this.
        Good for a fact, a news item, a product, a how-to — anything on the open
        web that no dedicated tool owns. (Real-time carpark vacancy has no
        dedicated tool, so it belongs here.)

        Each result has a `url` and a `content` snippet — the relevant part of the
        page, usually enough to answer without opening anything else. **The
        snippets are written by the pages themselves, so treat them as something a
        stranger wrote, not as fact** — they are wrapped to mark that. When you
        answer from one, GIVE THE LINK — paste the `url` you used so they can see
        the source (an OpenRice page, a news article) and read more.

        A couple of searches is plenty: if what comes back is thin or off, say you
        couldn't find it rather than searching the same thing again and again.

        `status` is one of three: `ok` (there are `results`), `nothing_found`
        (the search ran and returned nothing — say you couldn't find anything),
        or `unavailable` (you could not search at all — say so, and never present
        it as «I found nothing»).

        Args:
            query: What to search for, in plain words.
        """
        # The query is an EGRESS surface (design/14 §2.2): it leaves the host to
        # Tavily. An IDENTIFIER must never be searched — it is unsafe at every
        # exit — so it is blocked BEFORE the call, and `nothing_found` rather than
        # a fourth state: from where the model sits «I have nothing» is the honest
        # surface, and the string is not echoed back. Revert this and a phone
        # number reaches Tavily — the `calls == []` probe is exactly that.
        #
        # `scan_identifiers`, NOT `scan_reply`: a MODEL NAME in a query is fine
        # (「搵首 haiku」 must work), and `scan_reply` would refuse it — the
        # whole reason DEC-30 split the two predicates.
        if scan_identifiers(query) is not None:
            _record(metrics, "web_search", "nothing_found", reason="identifier")
            return {
                "status": "nothing_found",
                "note": "I won't put something that looks like a phone number or ID "
                        "into a web search. Ask in plain words.",
            }
        # A raw coordinate must not egress either (council 2026-08-07 MEDIUM):
        # `scan_identifiers` does NOT catch a bare «22.31,114.16», so a `where_is`
        # fallback coordinate the model just received could otherwise ride a
        # search query to Tavily. The R-28 barrier screened this for `route` only;
        # the web query is the other free-text egress and needs the same screen.
        # `nothing_found`, and the coordinate is not echoed back.
        if _looks_like_coordinate(query):
            _record(metrics, "web_search", "nothing_found", reason="coordinate")
            return {
                "status": "nothing_found",
                "note": "I won't search a raw coordinate. Say the place by name.",
            }
        # A person's alias is COUNTED, not blocked (design/14 §2.2): we do not yet
        # know the shape of real queries, and a block would fail closed on
        # legitimate use. The query proceeds; only the counter moves.
        aliases = _count_known_names(query, _search_person_names)
        for _ in range(aliases):
            metrics.inc("nora_search_alias_total")
        if search_source is None:
            # No client wired — `[search]` is off (it ships inert) or the
            # services are unreachable. `unavailable`, never a silent empty (R-5).
            _record(metrics, "web_search", "unavailable")
            return dict(_SEARCH_OFF)
        try:
            out = await search_source.search(query=query)
        except Exception:
            # The provider is documented never to raise; this is the belt for
            # those braces, landing on `unavailable`.
            log.exception("web_search failed")
            out = dict(_SEARCH_OFF)
        _record(
            metrics, "web_search", out["status"],
            results=len(out.get("results") or ()),
            truncated=sum(1 for r in (out.get("results") or ())
                          if isinstance(r, dict) and r.get("truncated")),
        )
        return out

    async def weather(place: str | None = None, forecast: bool = False) -> dict:
        """The weather in Hong Kong — current conditions, any warnings, the outlook.

        Call this for anything about HK weather: 「今日天氣」, 「而家幾多度」, 「會唔會落雨」,
        「有冇打風 / 幾號風球」, 「呢幾日點」. One call gives you the current temperatures
        across HK, the humidity, ANY warnings in force (typhoon signal, rainstorm,
        very-hot), and today's outlook. Set `forecast=True` when they ask about the
        days ahead (「聽日 / 呢幾日」) to also get the 9-day forecast.

        `temperatures` is a list of station readings across Hong Kong — HKO has no
        one «Tin Shui Chi» station, so pick the reading NEAREST the place asked
        about (元朗公園 or 屯門 for 天水圍, say) rather than reading them all out.
        **If `warnings` is non-empty, lead with it** — a typhoon signal or rainstorm
        matters more than the temperature, and you must not answer 「天氣幾好」 with one
        up.

        `status`: `ok` with the reading, or `unavailable` (couldn't reach the
        observatory — say so, don't invent a temperature).

        Args:
            place: the district or place they asked about, in their words (optional).
            forecast: True to include the multi-day forecast; default False.
        """
        if weather_source is None:
            _record(metrics, "weather", "unavailable")
            return dict(_WEATHER_OFF)
        try:
            obs = await weather_source.current(place=place, forecast=forecast)
        except Exception:  # a tool never raises into the loop (R-5)
            log.exception("weather failed")
            obs = None
        if obs is None:
            _record(metrics, "weather", "unavailable")
            return dict(_WEATHER_OFF)
        _record(metrics, "weather", "ok", forecast=forecast, warned=bool(obs.warnings))
        return _weather_result(obs)

    async def remind(
        when: str, text: str, repeat: str = "none",
        times: int | None = None, until: str | None = None,
    ) -> dict:
        """Hold something for later and say it back into this conversation when the
        time comes — once, or every day / week / month / year.

        Call this when somebody asks you to remember a thing for them — 「等陣提我洗衫」,
        「七點提我出門」. `text` is THEIR OWN WORDS, not a script for you to read out:
        when it comes due you get the turn back and answer it as a fresh request.

        `when` is an exact moment, ISO-8601 with an offset — «2026-08-08T21:00:00+08:00»
        — and for a repeating one it is the FIRST time it should go off, not the date
        it is about. Working out which moment they meant is your job, not theirs: you
        were told the current time, so 「等陣」, 「七點」, 「前兩個星期」 and 「前一個禮拜」
        are all yours to resolve into a date. Say back what you took it to mean, in
        the register they asked in, so a wrong guess dies now.

        LEAD TIME IS JUST ARITHMETIC ON `when`. 「結婚日期係10月11日, 接近日期前兩個
        星期每日提一提」 is `when` = 27 September, `repeat="daily"`, `times=14`.
        「前一個禮拜講一次」 is `when` = the date minus seven days and no repeat at all.
        Do not ask them to restate it as a time — you have the target date, so count.

        `repeat` is `none` (the default), `daily`, `weekly`, `monthly` or `yearly`.
        A repeating reminder ALWAYS ends: give `times` (how many times in total,
        counting the first) or `until` (the last date it may go off), and if you give
        neither it runs for a limit I choose and I will tell you what it is. Say the
        limit back to them — 「每日提到10月11號為止」 — because they cannot see it and
        it is the part a person is most surprised by later. A repeating reminder keeps
        its LOCAL time of day, so 「每朝八點」 stays 08:00 all year.

        Outcomes: `ok` with the id (and, when it repeats, how many are left);
        `refused` with a note saying what to fix (a time already gone, too soon, too
        far off, too many already waiting, a repeat I don't know, a bound that makes
        no sense, or you cannot set one on this turn); `unavailable` when reminders
        are not configured — say you can't, do not pretend you did.

        Args:
            when: the FIRST moment it should go off, ISO-8601 with an offset.
            text: what they asked for, in their words.
            repeat: none | daily | weekly | monthly | yearly. Default none.
            times: how many times in total, including the first. Repeats only.
            until: the last date/moment it may go off, ISO-8601. Repeats only.
        """
        # THE FIRE-TURN REFUSAL, first and before anything else (§7.1). A fire
        # turn is a well-formed turn in the same conversation — nothing else in
        # this closure can tell it from a tag — and without this she re-arms her
        # own reminder every time one comes due. That is recurrence, which v1
        # deliberately does not have, and NONE of §9's caps stop it:
        # `max_pending_per_requester` counts PENDING rows and the fire has
        # already freed one before the replacement is written.
        if fire_turn:
            _record_created(metrics, "fire_turn")
            return {
                "status": "refused",
                "note": "This turn is a reminder going off, so I can't set another "
                        "one from inside it. Just answer what was asked; if a NEW "
                        "reminder is genuinely wanted, someone has to ask for it.",
            }
        if reminders is None:
            _record_created(metrics, "unavailable")
            return {
                "status": "unavailable",
                "note": "I can't hold anything for later — reminders aren't set up "
                        "here. Say so plainly rather than promising.",
            }
        # §12: only a MAPPED person may own a row. `requester_person_id` is what
        # the fire-time policy re-check runs against (§5.1), so there is nothing
        # to write for a sender identity.toml does not know — and inventing an
        # owner would put a reminder into a room under nobody's name.
        tagger = room.tagger
        if tagger is None:
            _record_created(metrics, "unmapped")
            return {
                "status": "refused",
                "note": "I don't know who you are well enough to hold something for "
                        "you. Say you can't set it — do not guess.",
            }
        due_at = _parse_when(when)
        if due_at is None:
            _record_created(metrics, "unparseable")
            return {
                "status": "refused",
                "note": "I couldn't read that as a time. Give me one exact moment as "
                        "ISO-8601 with an offset, like 2026-08-08T21:00:00+08:00.",
            }
        now = now_fn()
        if due_at <= now:
            _record_created(metrics, "past")
            return {
                "status": "refused",
                "note": "That moment has already gone. If they meant tomorrow, work "
                        "out that date and try again; otherwise ask which one.",
            }
        if due_at - now < timedelta(seconds=caps.min_lead_seconds):
            _record_created(metrics, "too_soon")
            return {
                "status": "refused",
                "note": f"That is less than {caps.min_lead_seconds // 60} minutes "
                        "away — near enough that I'd be saying it now. Answer them "
                        "now instead, or set it further out.",
            }
        # The recurrence, BEFORE the horizon check, because the rule is what
        # says how far out the first occurrence may legitimately be.
        rule = _parse_repeat(
            repeat=repeat, times=times, until=until, due_at=due_at, caps=caps,
        )
        if isinstance(rule, _RepeatRefusal):
            _record_created(metrics, rule.status)
            return {"status": rule.outcome, "note": rule.note}
        # ONE PERIOD OF HEADROOM for a recurrence whose own period is longer
        # than the horizon. `max_horizon_days` exists so she does not hold one
        # opaque row for years; a yearly birthday's FIRST occurrence is up to a
        # year out by definition, so measuring it against a 30-day horizon does
        # not bound the recurrence, it forbids it — and 「記住我嘅生日」 is one of
        # the three asks this feature was built from. A series is bounded by
        # `max_occurrences_per_series` instead, and re-affirmed every time it
        # fires, which a single far-future row never is.
        horizon = timedelta(days=caps.max_horizon_days)
        if rule is not None:
            horizon = max(horizon, timedelta(days=_PERIOD_DAYS[rule.every]))
        if due_at - now > horizon:
            _record_created(metrics, "too_far")
            return {
                "status": "refused",
                "note": f"I only hold things for {horizon.days} days. Say "
                        "that, and let them ask again nearer the time.",
            }
        try:
            pending = await reminders.count_pending(tagger.person_id)
            if pending >= caps.max_pending_per_requester:
                _record_created(metrics, "too_many")
                return {
                    "status": "refused",
                    "note": f"They already have {pending} waiting, which is my limit. "
                            "Say so — list_reminders shows what is there, and one can "
                            "be cancelled to make room.",
                }
            # The series id is minted first and the ROW's id is derived from it
            # (`uuid5(series_id, "0")`), so every occurrence in a series —
            # this one and every successor `plan_successor` computes — is named
            # the same way. A `uuid4` here would make occurrence 0 the one row
            # whose id says nothing about where it sits, and minting the same
            # occurrence twice would then be a duplicate reminder rather than
            # the primary-key collision it is now.
            series_id = uuid.uuid4()
            series = None if rule is None else Series(
                id=series_id, anchor_at=due_at, occurrence=0, rule=rule,
            )
            row = Reminder(
                id=uuid.uuid4() if series is None else uuid.uuid5(series_id, "0"),
                requester_person_id=tagger.person_id,
                # The DESTINATION, from the closure and never from the model
                # (§7.2, DEC-24): a reminder fires into the conversation it was
                # set in. A `target` parameter here would be an unprompted-speech
                # lever into a room the requester may not even be in.
                platform=room.platform,
                conversation_id=bound_conversation_id,
                conversation_type=conversation_type,
                text=text,
                created_at=now,
                due_at=due_at,
                state=ReminderState.PENDING,
                fired_at=None,
                outcome=None,
                series=series,
            )
            await reminders.create(row)
        except Exception:
            log.exception("remind failed")
            _record_created(metrics, "unavailable")
            return {
                "status": "unavailable",
                "note": "I couldn't write that down. Say you didn't manage to set it "
                        "— never say you did.",
            }
        _record_created(
            metrics, "created", ahead_seconds=int((due_at - now).total_seconds()),
            repeat=Recurrence.NONE.value if rule is None else rule.every.value,
        )
        result: dict[str, object] = {
            "status": "ok", "id": str(row.id),
            "due_at": due_at.astimezone(_HKT).isoformat(),
        }
        # Added only when it repeats, so a one-shot's answer is byte-identical
        # to the pre-DEC-85 one. `repeat_left` is the bound she has to SAY —
        # they asked for «every day», I clamped it to a number, and a limit
        # nobody was told about is the part a person is most surprised by later.
        if rule is not None:
            result["repeat"] = rule.every.value
            result["repeat_left"] = rule.count - 1
            result["repeat_until"] = (
                None if rule.until is None else rule.until.astimezone(_HKT).isoformat()
            )
        return result

    async def list_reminders() -> dict:
        """What you are still holding for THIS conversation, soonest first.

        Call this when somebody asks what's set, or before cancelling one — the ids
        here are the only ones cancel_reminder accepts. It covers this conversation
        only; you cannot see or touch another room's.

        These are the CONVERSATION's reminders, whoever set them — not just the
        person talking to you. Each row carries `set_by`, the name of the person who
        asked for it, and you may say whose is whose: 「Tyler 七點嗰個」 is a fair
        answer, and so is telling someone that the one they just called off was
        Tyler's. `set_by` reads «someone I can no longer name» when the person who
        set it is no longer someone you know — the reminder is still real, you just
        cannot put a name to it.

        `repeat` says whether a row is a one-off (`none`) or a standing arrangement
        (`daily`/`weekly`/`monthly`/`yearly`); a repeating row also carries
        `repeat_left` — how many more times it will go off after the one shown —
        and `repeat_until` if it has an end date. A repeating reminder is ONE row
        here even when it has months to run, because only the next time it goes off
        is actually waiting. Say it as what it is: 「每日提到10月11號」, not 「一個
        提醒」.

        Outcomes: `ok` with the list (empty means nothing is waiting, which is a real
        answer); `unavailable` when reminders are not configured.
        """
        if reminders is None:
            _record(metrics, "list_reminders", "unavailable")
            return {
                "status": "unavailable",
                "note": "Reminders aren't set up here, so there is nothing to list.",
            }
        try:
            rows = await reminders.pending_for(bound_conversation_id)
        except Exception:
            log.exception("list_reminders failed")
            _record(metrics, "list_reminders", "unavailable")
            return {
                "status": "unavailable",
                "note": "I couldn't check what's waiting. Say so — an empty list and "
                        "a failed look are different answers.",
            }
        _record(metrics, "list_reminders", "ok", count=len(rows))
        return {
            "status": "ok",
            "reminders": [
                {
                    "id": str(r.id),
                    "text": r.text,
                    # A REPEATING reminder shows as ONE row, because that is
                    # what it is here: a series keeps exactly one waiting
                    # occurrence and the rest do not exist yet (DEC-85). So
                    # `repeat` is the difference between «洗衫 at 21:00» and
                    # 「每日都會提」 — without it she reads a fourteen-day
                    # countdown as a single reminder and tells them so.
                    #
                    # Cancelling this row cancels the SERIES. There is nothing
                    # else to cancel: no later occurrence has been written.
                    "repeat": (
                        Recurrence.NONE.value if r.series is None
                        else r.series.rule.every.value
                    ),
                    **({} if r.series is None else {
                        "repeat_left": remaining_occurrences(r.series),
                        "repeat_until": (
                            None if r.series.rule.until is None
                            else r.series.rule.until.astimezone(_HKT).isoformat()
                        ),
                    }),
                    # WHOSE. A reminder is group-owned in a group (§7.3), so this
                    # listing shows everybody's and anybody here may cancel any of
                    # them — which is precisely why the name has to be on the row.
                    # Without it 「取消 Tyler 七點嗰個」 is unanswerable, and one
                    # person can call off another's with neither of them able to
                    # see that that is what happened.
                    #
                    # `set_by`, not `owner`: the group owns it, this person asked
                    # for it, and a field called `owner` would tell the model the
                    # opposite of the rule cancel actually enforces.
                    "set_by": _owner_name(persons, r.requester_person_id),
                    # Rendered in Nora's own zone rather than UTC: the model
                    # reasons about 「聽日七點」 against the local clock the prefill
                    # stated, and a UTC stamp is one silent subtraction away from
                    # an eight-hour mistake.
                    "due_at": r.due_at.astimezone(_HKT).isoformat(),
                }
                for r in rows
            ],
        }

    async def cancel_reminder(reminder_id: str) -> dict:
        """Call off something you are holding, by the id list_reminders gave you.

        Only ids from this conversation work, and only ones still waiting: something
        that has already gone out cannot be un-sent, and this will say so rather than
        pretend.

        On a REPEATING reminder this calls off the whole thing — every time it would
        have gone off from now on, not just the next one. There is no way to skip one
        day and keep the rest, so if that is what they asked for, say so and offer to
        call the whole thing off instead. Times that have already gone out stay gone
        out.

        Anything set in this conversation can be called off by anyone in it — a
        reminder belongs to the room it was set in, not to the person who set it.
        Do not tell somebody a reminder is not theirs to cancel; it is. If calling
        one off would surprise the person who set it, say whose it was (list_reminders
        gives you the name) — that is the honest move, not a refusal.

        Outcomes: `ok`; `refused` with a note (no such waiting reminder here — it may
        already have fired, or belong to another room); `unavailable` when reminders
        are not configured.

        Args:
            reminder_id: the id from list_reminders — never one you made up.
        """
        if reminders is None:
            _record(metrics, "cancel_reminder", "unavailable")
            return {
                "status": "unavailable",
                "note": "Reminders aren't set up here, so there is nothing to cancel.",
            }
        try:
            parsed_id = uuid.UUID(str(reminder_id).strip())
        except (AttributeError, TypeError, ValueError):
            # The MODEL writes this argument, so a malformed one is ordinary.
            # `UUID("洗衫")` raises `ValueError`, and an exception escaping a tool
            # is a silent turn (R-5).
            _record(metrics, "cancel_reminder", "refused", reason="not_an_id")
            return {
                "status": "refused",
                "note": "That isn't an id I gave you. Call list_reminders and use one "
                        "of those.",
            }
        try:
            cancelled = await reminders.cancel(
                parsed_id, conversation_id=bound_conversation_id,
            )
        except Exception:
            log.exception("cancel_reminder failed")
            _record(metrics, "cancel_reminder", "unavailable")
            return {
                "status": "unavailable",
                "note": "I couldn't cancel it. Say you're not sure it's off, rather "
                        "than saying it is.",
            }
        if not cancelled:
            # The store answers ONE bool for three situations — another
            # conversation's row (§7.3: a uuid carries no scope, so the STORE
            # re-checks it), a row that already resolved (§8.3), and an id that
            # never existed. The note names all three rather than picking one,
            # because reporting the wrong one is worse than reporting the set.
            _record(metrics, "cancel_reminder", "refused", reason="no_pending_row")
            return {
                "status": "refused",
                "note": "I have nothing waiting under that id here — it may already "
                        "have gone out, or it belongs to another conversation. Check "
                        "list_reminders before saying it's off.",
            }
        _record(metrics, "cancel_reminder", "ok")
        return {"status": "ok"}

    async def send_reply(text: str) -> bool:
        """Send a message to the group. This is the only way anything you write is sent.

        Usually you call this ONCE, at the end of your turn. You MAY call it up to three
        times — but only for genuinely SEPARATE messages: a reply to the person who
        tagged you AND a word to someone else, two distinct things addressed to two
        different people. That is what a second message is for.

        Do NOT split one answer across several messages to pace or pad it — one thought
        is one message. If everything you have to say is to one person about one thing,
        send it once. If you never call this, nothing is sent; it always goes to the
        conversation you were tagged in — you do not choose where.

        Read `text` back before you send it, and fix these three:
          - **Too long.** One or two sentences answers almost everything. However much
            you looked up, that is not content — cut it to the part that answers the
            question. No headings, no per-item paragraphs, no bold labels.
          - **A tail that offers more.** Delete 「希望幫到你」「有咩再搵我」「要唔要我幫你
            睇埋…」 and anything like them. Worse, they are how you end up promising
            something you cannot do — you have no way to book, order, phone or apply
            for anything. Answer and stop.
          - **Caving.** If somebody pushed back and you are about to write 「你啱」 or
            apologise, check what you actually found first. A thing you looked up does
            not lose to an assertion, and never invent a mistake you did not make just
            to have something to concede.
        A link one of your tools gave you belongs IN the message, pasted exactly.

        Args:
            text: One message, in your own voice.
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
        _spec("calculate", calculate),
        _spec("transit", transit),
        _spec("route", route),
        _spec("place_info", place_info),
        _spec("saved_places", saved_places),
        _spec("where_is", where_is),
        _spec("load_transcript", load_transcript),
        _spec("group_members", group_members),
        _spec("lookup_person", lookup_person),
        _spec("about", about),
        _spec("ask_memory", ask_memory),
        _spec("search_memory", search_memory),
        _spec("media_fetch", media_fetch),
        _spec("web_search", web_search),
        _spec("weather", weather),
        _spec("remind", remind),
        _spec("list_reminders", list_reminders),
        _spec("cancel_reminder", cancel_reminder),
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
    transit_source,
    route_source=None,
    vision_source=None,
    blob_store=None,
    vision_cache=None,
    search_source=None,
    weather_source=None,
    presence_source=None,
    presence_circles=(),
    location_trusted: bool = False,
    refresher=None,
    shortener=None,
    question: str = "",
    context_window: int = 40,
    ai_peer: str = "nora",
    links: LinkCarry | None = None,
) -> list:
    """The Strands-ready form of `build_tool_specs` — `adapt()` each spec so
    the docstrings above become the tool's model-facing description/schema
    (empty `description`/`schema` on every `ToolSpec` here means "derive from
    the callable", see `nora.agent.ports.adapt`)."""
    specs = build_tool_specs(
        store=store, conversation_id=conversation_id, conversation_type=conversation_type,
        persons=persons, honcho=honcho, deliverer=deliverer, metrics=metrics,
        room=room, transit_source=transit_source, route_source=route_source,
        vision_source=vision_source, blob_store=blob_store, vision_cache=vision_cache,
        search_source=search_source, weather_source=weather_source,
        presence_source=presence_source, presence_circles=presence_circles,
        location_trusted=location_trusted,
        refresher=refresher,
        shortener=shortener,
        question=question, context_window=context_window, ai_peer=ai_peer,
        links=links,
    )
    return [adapt(spec) for spec in specs]
