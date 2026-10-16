"""The authored framing text around the blocks: ONE place, typed builders.

A context block says what it is in its own opening tag (`nora.domain.blocks`).
Around the blocks Nora's own code also says things in plain sentences: why a
fire turn is happening and what is asked of her, who tagged her under an
Instagram post, what time it is, how the turn opens and ends. They are not
blocks, because a `ContextBlock` cannot render without an opening tag and giving
these one would change what the model reads; they are typed, not XML. One place
stops each call site escaping its own values its own way, and stops another
being written inline.

**One builder per piece of text, one function for every value that enters it.**
A builder is a frozen dataclass whose fields are the inputs, typed, and whose
`render()` returns the text. Every person- or store-controlled value enters the
text through `_put`, and `_put` is the only function here that calls `escape`.
How a value enters is NAMED at the call, one of four, so a reviewer reads the
policy off the line and the architecture test pins every non-default one
(`tests/architecture/test_framing_is_one_typed_place.py`):

* `"escaped"`: R-13's escape (`nora.domain.prompt.escape`), the default;
* `"carried"`: the text is the BODY of a block that escapes it once itself
  (`build_tag_message` escapes `<tag_message>`), so escaping it here would
  escape it twice. Formatted as an f-string formats it, so any object goes in;
* `"joined"`: `"carried"` for a text that master joined with `str.join`, which
  REFUSES a non-str with a `TypeError`. A reminder's text is one: a turn whose
  row held a `None` failed closed, and must still;
* `"system"`: computed by code from the clock, the zone database or a number,
  or the output of another builder; no person authored it.

There is no fifth way. Nothing reaches the model unescaped except what a
block escapes for itself or what code, not a person, computed.

**Byte-exact.** `tests/unit/test_headers_goldens.py` holds the rendered bytes.
Changing a word is a model-visible change and needs the eval suite.

`FIRE_HEADERS` is the table: every kind of fire `Event` has exactly one header
builder in it, and `FRAMING` lists every builder this module declares.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from nora.domain.compute import DocumentReadBack
from nora.domain.models import PersonRef
from nora.domain.ports import JobResult, JobStatus
from nora.domain.prompt import age, escape
from nora.domain.reminders import Recurrence, Reminder, Series, remaining_occurrences

Entry = Literal["escaped", "carried", "joined", "system"]


def _put(value: object, how: Entry = "escaped") -> str:
    """The ONE function a value passes through to enter authored text.

    `"escaped"` is `nora.domain.prompt.escape`: `& < >` and the control
    characters XML cannot carry. The other three leave the bytes as they are and
    differ in what they say about why (see the module docstring); `"joined"`
    also refuses a non-str, as `str.join` did where this text used to be built."""
    if how == "escaped":
        return escape(value)
    if how == "joined" and not isinstance(value, str):
        raise TypeError(f"expected str instance, {type(value).__name__} found")
    return str(value)


def _figure(value: float, spec: str = "") -> str:
    """A number as text. A number carries no markup and no person's words; this
    is the one way one enters authored text, so it is a call like any other, and
    a string cannot be smuggled in past the placeholder check through it: with no
    format spec anything but a number is refused, and with `".2f"` (the only spec
    used) `format` itself refuses a string, with the `ValueError` the f-string it
    replaced raised."""
    if not spec and not isinstance(value, (int, float)):
        raise TypeError(f"a number is expected, not {type(value).__name__}")
    return format(value, spec)


class Header(Protocol):
    """A situation paragraph: what `Event.header` renders for a fire turn."""

    def render(self) -> str: ...


# ---- how a recurrence reads, and a stamp ------------------------------------

#: How a recurrence reads in the fire header. English prose, like the rest of the
#: header: the header is the SITUATION she is told about, and `doctrine` decides
#: what language she answers in.
#:
#: EVERY member of `Recurrence` except NONE must be here: `RepeatClause`
#: subscripts this bare, and a missing unit would raise KeyError inside the fire
#: header after `claim` minted the successor and before the model is called, so
#: the occurrence would be claimed, nothing said, and the next one armed.
#: `tests/unit/test_fire_path.py` covers the shape.
CADENCE_UNITS: Mapping[Recurrence, str] = {
    Recurrence.MINUTELY: "minute",
    Recurrence.HOURLY: "hour",
    Recurrence.DAILY: "day",
    Recurrence.WEEKLY: "week",
    Recurrence.MONTHLY: "month",
    Recurrence.YEARLY: "year",
}


def stamp(moment: datetime) -> str:
    """A due/set time in the reader's own timezone. Local, not UTC: the rest of
    the prompt states time locally (`ClockLine`), and two clocks in one prompt
    is how she answers with the wrong hour."""
    return moment.astimezone().strftime("%-d %b %H:%M")


@dataclass(frozen=True)
class RepeatClause:
    """« (every day, 11 more after this) » — or nothing at all for a one-shot.

    `remaining_occurrences` is the series' COUNT bound, so this over-states when
    an `until` or a cancel will end the series sooner. That is the right way to
    be wrong here: the number is context for her tone, not a promise she is
    asked to repeat, and the header already tells her not to read the times back
    unless they asked in those terms.
    """

    series: Series | None

    def render(self) -> str:
        series = self.series
        if series is None:
            return ""
        left = remaining_occurrences(series)
        more = "last one" if left == 0 else f"{_figure(left)} more after this"
        # The INTERVAL, not just the unit: `rule.every` alone would render a
        # fortnightly arrangement as «every week» and a ten-minute one as
        # «every minute», wrong by the whole multiplier.
        step = series.rule.interval
        unit = CADENCE_UNITS[series.rule.every]
        cadence = unit if step == 1 else f"{_figure(step)} {unit}s"
        return f" (every {cadence}, {more})"


def _repeat(series: Series | None) -> str:
    return RepeatClause(series).render()


def _name(person: PersonRef | None) -> str:
    """The name a header says, escaped. A header's `tagger` is typed optional
    because `Event.header` is one callable type for every occasion; an occasion
    nobody asked for renders an empty name, as it always did."""
    return _put(person.name if person is not None else "")


# ---- the fire headers: one per kind of `Event` ------------------------------


@dataclass(frozen=True)
class ReminderHeader:
    """The situation paragraph for a reminder fire turn — `build_prefill`'s
    `header`.

    Every clause of the tag header is false here: nobody called her, and that is
    not why this turn is happening. `ContextHeader` is parameterised rather than
    branched so the block catalogue, the notation and — the part that actually
    matters — R-13/R-13b escaping stay shared.

    The name is escaped because everything else a prompt carries is, and
    `identity.toml` is operator-authored text like any other.

    `tagger` is typed optional only because `Event.header` is one callable type
    for both occasions; a reminders occasion always names a requester, and a turn
    whose requester fell out of `identity.toml` is refused by `FirePath._turn`
    before this is ever rendered.
    """

    rows: tuple[Reminder, ...]
    tagger: PersonRef | None

    def _asked(self) -> str:
        return "".join(
            f"- asked {stamp(row.created_at)}, due {stamp(row.due_at)}"
            f"{_repeat(row.series)}\n" for row in self.rows
        )

    def _repeats(self) -> str:
        # Stated only when it is true, so a one-shot turn's prompt carries no
        # repeat text (DEC-85). Without it she cannot know this is the third of
        # fourteen and announces a standing arrangement as if it were new
        # («記住喇, 我會日日提你») every day.
        if any(row.series is not None for row in self.rows):
            return (
                "\nSome of these repeat, as marked. A repeat is not a new "
                "arrangement — you already told them you would do this, so bring "
                "the thing up rather than announcing the reminder again.\n"
            )
        return ""

    def _request(self) -> str:
        return "request" if len(self.rows) == 1 else "requests"

    def render(self) -> str:
        return (
            "You are in a group chat you have been reading all along. Nobody has "
            f"just called you. Earlier, {_name(self.tagger)} asked you to bring "
            "something back up at a particular time, and that time has come — "
            "that is why this turn is happening, and it is the only reason.\n"
            f"When the {self._request()} in tag_message below were made, and when "
            "each came due (in the same order):\n"
            f"{self._asked()}{self._repeats()}"
            f"Say it to {_name(self.tagger)} now, in your own words, as the "
            "person who was asked to remember. Do not read the times back at them "
            "unless they asked in those terms."
        )


@dataclass(frozen=True)
class ProactiveHeader:
    """The situation paragraph for a proactive act turn (DEC-162).

    Same opening shape as every fire header — nobody called her, and that is not
    why this turn is happening — with the reason clause that is true here and
    nowhere else: SHE decided, earlier, that this was worth raising at about this
    time. The right to decline is stated here, and only here: what a turn is
    belongs with the turn. The base rate («most turns like this one end that
    way») is stated here too.

    Times are stamped the way `ReminderHeader` stamps them — the reader's own
    zone, never UTC. The note title is escaped like every other value a prompt
    carries (R-13); it was model-chosen and code-resolved.

    **The closing clause is about the whole apparatus, not just the reminder.**
    The turn's prompt also carries the note's title, the `<notes>` index, and the
    intent her own earlier pass wrote, so the clause names the note beside the
    reminder, rules out the note's own words, and says what the whole of it is
    FOR: the reason she is here, never the content of what she says. One named
    forbidden sentence generalises to none.

    It deliberately does NOT say where what she says comes from instead,
    because the paragraph above already does («Read what is in front of you NOW —
    the transcript, the standing, the notes … and decide again»); repeating it
    adds risk and buys nothing.

    `nora.language.note_render.NOTES_BLOCK_NOTE` carries the same identity on the
    tier's own block, which is the half that reaches an ordinary tag turn.
    """

    rows: tuple[Reminder, ...]
    #: The turn's clock. The decision's age is rendered by the transcript's own
    #: `age`, so «written 43h ago» and a message «8h ago» compare at a glance:
    #: an absolute stamp beside relative ages left her unable to see that the
    #: message settling the thing came after the intention.
    now: datetime

    @staticmethod
    def _about(row: Reminder) -> str:
        payload = row.payload
        if isinstance(payload, Mapping) and isinstance(payload.get("note_title"), str):
            return f", about the note «{_put(str(payload['note_title']))}»"
        return ""

    def _decided(self) -> str:
        return "".join(
            f"- decided {stamp(row.created_at)} "
            f"({_put(age(int(row.created_at.timestamp() * 1000), self.now), 'system')}), "
            f"for {stamp(row.due_at)}"
            f"{self._about(row)}\n"
            for row in self.rows
        )

    def render(self) -> str:
        return (
            "You are in a group chat you have been reading all along. Nobody has "
            "called you — not just now, and not earlier. YOU decided, on your own "
            "pass over what this group has left open, that something here was "
            "worth raising at about this time, and wrote yourself the note below "
            "so you would come back to it. That time has come — that is why this "
            "turn is happening, and it is the only reason.\n"
            f"{self._decided()}"
            "The intention below is what you wrote to yourself then: an "
            "intention, not a script, and not a fact about the room. Every "
            "message in the transcript younger than that decision came after "
            "you wrote it, and you have not weighed it yet: if one of them "
            "settles the thing, it is settled. Read what "
            "is in front of you NOW — the transcript, the standing, the notes "
            "(note_read gives you a note's current body; where_is and route are "
            "worth asking now, not earlier) — and decide again. If the thing has "
            "since been settled, or somebody already said it, or it would not "
            "help, stay silent: silence is a complete answer here, not a failure, "
            "and most turns like this one end that way. "
            "If it is still worth saying, say it once, briefly, in your own words, "
            "and never as «I set myself a reminder» or «my note says», and never "
            "in the note's own words — the note and the reminder are WHY you are "
            "here, never WHAT you have to say."
        )


@dataclass(frozen=True)
class ComputeHeader:
    """The situation paragraph for a finished compute job. It names no job: what the
    job was and how it ended is the body's (`ComputeBody`), put to her as the
    request."""

    tagger: PersonRef | None

    def render(self) -> str:
        return (
            "You are in a group chat you have been reading all along. Nobody has "
            f"just called you. Earlier, {_name(self.tagger)} asked you for a piece "
            "of work, and it has now finished or failed — that is why this turn is "
            "happening, and it is the only reason.\n"
            "Tell them how it went, in your own words: what came back, with the link "
            "for each document if there is one, or what went wrong. Do not just "
            "repeat the raw report verbatim — say it the way you'd actually tell a "
            "friend."
        )


@dataclass(frozen=True)
class RelayHeader:
    """The situation paragraph for a message somebody asked to be passed into
    this room (DEC-105). It names the REQUESTER and the platform the request came
    from, both of the factory's own arguments, and ignores the turn's tagger."""

    requester: PersonRef
    from_platform: str

    def render(self) -> str:
        return (
            "You are in a group chat you have been reading all along. Nobody here "
            f"has just called you. {_put(self.requester.name)} asked you, over on "
            f"{_put(self.from_platform)}, to pass something on to this room — "
            "that is why this turn is happening, and it is the only reason.\n"
            "Pass it on, and make it unmistakable that "
            f"{_put(self.requester.name)} said it and not you. "
            "You are carrying somebody's message, not making a remark of your own."
        )


@dataclass(frozen=True)
class LostJobHeader:
    """The situation paragraph for a job whose delivery was lost across a
    restart. It must never say the job FAILED, and says so: that is a claim about
    a container nobody looked at."""

    tagger: PersonRef | None

    def render(self) -> str:
        return (
            "You are in a group chat you have been reading all along. Nobody has "
            f"just called you. Earlier, {_name(self.tagger)} asked you to build "
            "something and you said you were on it — then you were restarted and "
            "lost the thread of it. That is why this turn is happening, and it is "
            "the only reason.\n"
            "Own it briefly and tell them to ask again if they still want it. Do "
            "NOT say it failed and do NOT say it worked — you genuinely do not "
            "know which, and guessing either way would be a claim you cannot back."
        )


def _gate_note(relevance_score: float | None) -> str:
    """The Relevance Gate's score, as a sentence she may ignore.

    Rendered as what it IS — a cheap upstream guess made without her notes,
    facts or memory — and not as an instruction, because the moment it reads as
    one the second layer stops being a second judgement. The gate decided the
    room is ASKING; only this turn can decide whether she HOLDS anything, and the
    sentence has to leave that decision where it is.

    `None` renders nothing at all rather than "no score": a header that
    announced the absence would make the gate's own plumbing part of what she
    reads about the room.
    """
    if relevance_score is None:
        return ""
    return (
        f"\nA relevance check upstream scored this room {_figure(relevance_score, '.2f')} "
        "before you saw it. It read only the transcript and your standing — not "
        "your notes, facts or memory — so it is a guess about whether the room is "
        "asking, never a judgement that you have an answer. Weigh it as such, and "
        "stay silent anyway if you hold nothing."
    )


@dataclass(frozen=True)
class ParticipationHeader:
    """The situation paragraph for a participation turn.

    Same opening shape as the other fire headers — nobody called her, and that is
    not why this turn is happening — but the reason clause differs: no one asked
    for anything, earlier or otherwise. The room went quiet and the floor was
    offered. The right to decline it is stated HERE, and only here: what a turn
    is belongs with the turn, and the header is the sentence she reads first.
    Unlike `ProactiveHeader` it does NOT carry the base rate («most turns like
    this one end that way»): its effect here is unmeasured, and the Relevance
    Gate has already screened the window before this turn exists.
    """

    relevance_score: float | None = None

    def render(self) -> str:
        return (
            "You are in a group chat you have been reading all along. Nobody has "
            "called you — not just now, and not earlier. The conversation has gone "
            "quiet, and this turn is the offer of the floor, nothing more.\n"
            "The tag_message below is deliberately empty: nothing was put to you. "
            "Decide from what is already in front of you — the transcript above, "
            "and whatever standing and notes came with it. If speaking "
            "would add something the room can use, say it — once, briefly. If "
            "not, stay silent: silence is a complete answer here, not a failure."
            f"{_gate_note(self.relevance_score)}"
        )


#: kind of fire `Event` -> the ONE builder of its header. A kind absent from here
#: has no header, and `tests/architecture/test_framing_is_one_typed_place.py`
#: refuses an `Event` of a kind that is not in it, a kind built twice, and a
#: header written inline at the construction site.
FIRE_HEADERS: Mapping[str, type[Header]] = {
    "reminder": ReminderHeader,
    "proactive": ProactiveHeader,
    "compute": ComputeHeader,
    "relay": RelayHeader,
    "compute_lost": LostJobHeader,
    "participation": ParticipationHeader,
}


# ---- what a fire turn puts to her: the body of its `<tag_message>` ----------


@dataclass(frozen=True)
class RequestBody:
    """The requester's OWN sentence(s), joined, as the words put to her. That is
    the premise of a reminder: a deferred request, put to her at the due time AS a
    request, so the set-at and due-at facts belong in the header, never here.
    Carried: `build_tag_message` escapes the body once, in its block. Joined: a
    text that is not a str raises `TypeError`, as `"\n".join` did, so a row that
    somehow holds none fails the turn closed instead of putting «None» to her."""

    texts: tuple[str, ...]

    def render(self) -> str:
        return "\n".join(_put(text, "joined") for text in self.texts)


@dataclass(frozen=True)
class ComputeBody:
    """What a finished job puts to her, whatever worker ran it: what was asked, then what
    came back (the worker's report, falling back to its summary, and the documents code read
    back) or why it failed. A document id that did not read back is named missing and carries
    no link (R-100). Carried: every value is escaped once, by the `<tag_message>` block."""

    task: str
    result: JobResult
    documents: DocumentReadBack = field(default_factory=DocumentReadBack)
    #: Plain markdown from `nora.language.checkpoint_render` (mode `delivery`): where a job that
    #: STOPPED had got to, in the worker's own unchecked words. Empty for a job that finished
    #: (its report has the same sections) and for one that wrote none.
    checkpoint: str = ""
    #: Files the worker stored with `computer_export` to `keep`, as code saw it do it.
    kept_files: tuple[str, ...] = ()

    def _documents(self) -> str:
        made = "".join(
            f"\n- {_put(doc.title, 'carried')}: {_put(doc.url, 'carried')}" if doc.url
            else f"\n- {_put(doc.title, 'carried')}"
            for doc in self.documents.found
        )
        lost = ", ".join(_put(i, "carried") for i in self.documents.missing)
        kept = ", ".join(_put(f, "carried") for f in self.kept_files)
        return (
            (f"\nDocuments it made:{made}" if made else "")
            + (f"\nFiles it kept in the workspace: {kept}" if kept else "")
            + (
                "\nIt named these documents but they could not be found, so they are not "
                f"saved and have no link: {lost}" if lost else ""
            )
        )

    def _outcome(self) -> str:
        result = self.result
        if result.status is JobStatus.SUCCEEDED:
            link = f"\n{_put(result.url, 'carried')}" if result.url else ""
            return (
                f"It finished: {_put(result.report or result.summary, 'carried')}"
                f"{link}{self._documents()}"
            )
        where = (
            "\nWhat it had recorded when it stopped (its own words, not checked; what it "
            f"made is listed above):\n{_put(self.checkpoint, 'carried')}"
            if self.checkpoint else ""
        )
        return (
            f"It failed: {_put(result.error or 'no reason given', 'carried')}"
            f"{self._documents()}{where}"
        )

    def render(self) -> str:
        return f"(asked earlier) {_put(self.task, 'carried')}\n\n{self._outcome()}"


@dataclass(frozen=True)
class LostJobBody:
    """What a job lost across a restart puts to her: only the task, because
    nothing else is known. Carried, as `ComputeBody` is."""

    task: str

    def render(self) -> str:
        return f"(asked earlier) {_put(self.task, 'carried')}"


# ---- the tag prefill: header message, clock, closing line --------------------

#: The opening paragraph of `<context_header>` for a TAG: the situation, in the
#: model's own terms. Every clause of it is FALSE on a heartbeat fire turn (nobody
#: called her, and that is not why the turn is happening), so it is a named
#: default rather than a literal inside `ContextHeader`: the fire path passes its
#: own (`FIRE_HEADERS`), and the blocks and every escaping rule in
#: `nora.agent.prefill` are shared rather than copied. R-13/R-13b escaping is
#: pinned to `build_prefill` by two named acceptance tests, and a second prefill
#: rendering the same transcript would sit outside both.
TAG_HEADER = (
    "You are in a group chat you have been reading all along. You do not "
    "speak unless somebody calls you, and somebody just did — that is why "
    "this turn is happening."
)

#: The assistant message that closes the prefill: she is shown agreeing to read
#: and reply, so the turn that follows begins in her own voice.
CLOSING_LINE = "Understood. I'll read the conversation and reply as myself."

#: The body of `<spoken_turn>`: rendered only
#: when the message she is answering was spoken in a voice channel. The SITUATION,
#: not a rule list: what she is reading and where the reply goes. What follows
#: from it (no link read aloud, no list, no GIF, short) is hers to infer; telling
#: a model what it is reading works better than telling it what to do.
SPOKEN_TURN = (
    "This message was spoken aloud in the voice channel and reached you as a "
    "transcript. Your reply will be said aloud there by a voice: heard once, "
    "not read."
)


@dataclass(frozen=True)
class ContextHeader:
    """The body of `<context_header>`: how this turn works, told to the model in
    its own terms. `situation` is the paragraph a header builder rendered (or
    `TAG_HEADER`) and is the only part that differs between audiences; everything
    after it describes the blocks in front of her.

    **Deliberately free of anything that changes between tags**: this block is the
    head of the prompt and the provider's prefix cache matches on exact bytes, so
    the first byte that differs from the last request ends the hit for everything
    after it. The clock is `ClockLine`, which leads the volatile message instead
    (`nora.agent.prefill._context_header` carries the cache measurements).
    """

    situation: str

    def render(self) -> str:
        return (
            f"{_put(self.situation, 'system')}\n"
            "This is your material, not just scenery: what you know about these "
            "people is what makes a reply theirs rather than anybody's. Use it "
            "the way a friend who was there would, where it fits. Quoting "
            "somebody's own words back at them is for when they asked what was "
            "said, not for winning a point."
        )


@dataclass(frozen=True)
class ClockLine:
    """The current local time, stated plainly, as the first line of the volatile
    message. `stated` is `nora.agent.clock.state_time` of the instant, the same
    formatter the `now` tool answers with, so a mid-turn `now()` states exactly
    this line's facts."""

    stated: str

    def render(self) -> str:
        return f"Right now it is {_put(self.stated, 'system')}.\n"


# ---- the Instagram summons' head ---------------------------------------------


def _utc_minute(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M")


@dataclass(frozen=True)
class SummonsHead:
    """What she is reading, in our own sentences: the first line of the summons'
    prompt, outside every block (a block cannot render without an opening tag).

    `now` prints its own digits under a literal `UTC`, whatever zone it carries:
    the summons' context is built from a UTC clock, and a zone-aware non-UTC `now`
    would be mislabelled (pinned by the goldens, not changed here).

    `summoned_by` is `@username` or `someone`. It is escaped, and escaping it
    changes no byte for any value that can reach it: a summoner handle is
    `[A-Za-z0-9._]{1,30}` (`nora.domain.instagram_links.is_handle`, both
    construction sites filter), and `escape` is the identity on every one of
    those characters. Escaping it means a path that sets the summoner without
    that filter cannot inject into the first line.

    `max_chars` is the reply's hard limit (`PUBLIC_REPLY_MAX_CHARS`), stated as
    a fact about this turn: a reply over it is refused whole as `too_long`, and
    the doctrine names no number.
    """

    now: datetime
    summoned_by: str
    summoned: str
    max_chars: int

    def render(self) -> str:
        return (
            "You were tagged in a comment under a public Instagram post. Whatever "
            "you send is posted once, as a public reply to that comment, where "
            f"strangers read it. A reply longer than {_figure(self.max_chars)} characters "
            "is not posted at all. Saying nothing is a complete answer. It is now "
            f"{_utc_minute(self.now)} UTC. {_put(self.summoned_by)} tagged "
            f"you {_put(self.summoned, 'system')}. Under each item of the post is "
            "what a reading of it before this turn found, or that it was not read."
        )


#: Every typed builder this module declares, by name. The architecture test holds
#: it equal to the classes the module defines, so a builder cannot exist outside
#: the table and a table row cannot outlive its builder.
FRAMING: Mapping[str, type] = {
    cls.__name__: cls for cls in (
        RepeatClause, ReminderHeader, ProactiveHeader, ComputeHeader,
        RelayHeader, LostJobHeader, ParticipationHeader,
        RequestBody, ComputeBody, LostJobBody, ContextHeader, ClockLine, SummonsHead,
    )
}
