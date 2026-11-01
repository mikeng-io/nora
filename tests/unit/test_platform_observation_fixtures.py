"""`tests/fixtures/platform_observations` — generated native protocol vectors,
validated against the parsers and pinned SDK seam they feed, not against their
own `semantics` prose.

Two bindings, by transport:

* Signal fixtures are literal signal-cli 0.14.7 SSE ``data:`` lines — the exact
  dict `parse_event` accepts. The test proves each payload reaches the inner
  parse seam (envelope framing and group admission pass, and the declared
  native field is present in the dataMessage) and asserts the current
  outcome — a `Touch` for the captured reaction family, `None` for every
  family with no parse branch. A `None` expectation here is a gap pin, not a
  claim the payload is wrong: when the capture lane lands its branch, that
  expectation is updated with the fixture set.

* Discord fixtures are Gateway dispatch frames (`op`/`t`/`s`/`d`). The test
  proves the pinned discord.py 2.7.1 `ConnectionState` still carries each
  declared `parse_<event>` — upstream capability evidence — hydrates the real
  `Raw*Event` DTOs from `d` where the SDK constructs them without connection
  state, and feeds supported families through `parse_discord_*` for the real
  contract: `Touch` for reaction/delete, the `[poll]`/`[forwarded message]`
  markers for message-carried shapes, `None` for the poll-close non-edit and
  the DM admission boundary.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import discord
import pytest
from discord.partial_emoji import PartialEmoji
from discord.raw_models import (
    RawMemberRemoveEvent,
    RawMessageDeleteEvent,
    RawPollVoteActionEvent,
    RawReactionActionEvent,
    RawReactionClearEmojiEvent,
    RawReactionClearEvent,
    RawThreadDeleteEvent,
)
from discord.state import ConnectionState

from nora.domain.models import EditTarget, Touch
from nora.domain.observations import (
    MembershipPayload,
    Observation,
    PinPayload,
    PollPayload,
)
from nora.platform.discord.parser import (
    parse_discord_channel_update,
    parse_discord_delete,
    parse_discord_edit,
    parse_discord_member_add,
    parse_discord_member_remove,
    parse_discord_member_update,
    parse_discord_message,
    parse_discord_message_metadata,
    parse_discord_pin_message,
    parse_discord_pins_update,
    parse_discord_poll_close,
    parse_discord_poll_definition,
    parse_discord_poll_vote,
    parse_discord_reaction,
    parse_discord_reaction_clear,
    parse_discord_role_update,
    parse_discord_thread_create,
    parse_discord_thread_delete,
    parse_discord_thread_update,
)
from nora.platform.signal.parser import (
    _data_message,
    extract_envelope,
    parse_event,
    parse_observations,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "platform_observations"
SIGNAL = sorted((FIXTURES / "signal").glob("*.json"))
DISCORD = sorted((FIXTURES / "discord").glob("*.json"))

SIGNAL_OWN = "+85290000100"
DISCORD_OWN = "123456789012345999"


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


# ── fixture integrity and the pins they document ─────────────────────────────


def test_every_fixture_marks_itself_generated() -> None:
    for path in (*SIGNAL, *DISCORD):
        doc = _load(path)
        assert doc["meta"]["generated"] is True, path.name
        assert doc["meta"]["case"], path.name
        assert doc["semantics"]["object"], path.name


def test_signal_fixtures_pin_the_deployed_signal_cli_version() -> None:
    """`meta.native_version` cannot drift from the image's own pin."""
    dockerfile = (ROOT / "deploy" / "signal-cli" / "Dockerfile").read_text()
    m = re.search(r"SIGNAL_CLI_VERSION=(\S+)", dockerfile)
    assert m, "deploy/signal-cli/Dockerfile lost its version pin"
    for path in SIGNAL:
        assert _load(path)["meta"]["native_version"] == m.group(1), path.name


def test_discord_fixtures_pin_the_locked_discord_py_version() -> None:
    """`meta.native_version` must name the version uv actually locked and installed."""
    assert discord.__version__ == "2.7.1", "the installed discord.py is not the pinned one"
    assert f"discord_py-{discord.__version__}" in (ROOT / "uv.lock").read_text()
    for path in DISCORD:
        assert discord.__version__ in _load(path)["meta"]["native_version"], path.name


def test_discord_payload_is_the_declared_dispatch_frame() -> None:
    for path in DISCORD:
        doc = _load(path)
        frame = doc["payload"]
        assert frame["op"] == 0 and frame["t"] == doc["meta"]["dispatch"], path.name
        assert isinstance(frame["d"], dict), path.name


def test_signal_payload_is_the_sse_envelope_framing() -> None:
    """The live transport's framing — no `method`, envelope at top level —
    is the one every fixture uses (see `extract_envelope`'s docstring)."""
    for path in SIGNAL:
        payload = _load(path)["payload"]
        assert "method" not in payload and isinstance(payload.get("envelope"), dict), path.name


# ── the pinned SDK still delivers every declared Discord event ────────────────


def test_every_declared_dispatch_exists_on_the_pinned_connection_state() -> None:
    """`parse_<event>` on `ConnectionState` is the capability boundary: its
    presence proves discord.py 2.7.1 hands this dispatch to a handler. Capture
    is the app's subscription; that is a different, still-missing step."""
    for path in DISCORD:
        parse = _load(path)["meta"]["discord_py_parse"]
        assert callable(getattr(ConnectionState, parse, None)), (
            f"{path.name}: pinned discord.py has no {parse}"
        )


# ── Signal: the wire reaches the seam; the branch is what is missing ─────────

# filename -> the inner dataMessage field the vector exercises, or None when
# the vector deliberately carries no dataMessage (typing).
_SIGNAL_FIELD = {
    "poll_create.json": "pollCreate",
    "poll_vote.json": "pollVote",
    "poll_vote_retract.json": "pollVote",
    "poll_terminate.json": "pollTerminate",
    "pin_message.json": "pinMessage",
    "unpin_message.json": "unpinMessage",
    "admin_delete.json": "adminDelete",
    "group_update.json": "groupInfo",
    "reaction_add.json": "reaction",
    "reaction_orphan_target.json": "reaction",
    "poll_vote_missing_target.json": "pollVote",
    "poll_create_dm.json": "pollCreate",
    "typing_message.json": None,
    "shared_contact.json": "contacts",
    "link_preview.json": "previews",
}


@pytest.mark.parametrize("path", SIGNAL, ids=lambda p: p.name)
def test_signal_payload_reaches_the_inner_parse_seam(path: Path) -> None:
    """Framing and (where required) group admission pass — so the bytes DO
    arrive; only the missing parse branch drops them."""
    doc = _load(path)
    assert doc["meta"]["upstream"].endswith(_SIGNAL_FIELD[path.name] or "typingMessage")
    env = extract_envelope(doc["payload"])
    assert isinstance(env, dict), path.name
    field_name = _SIGNAL_FIELD[path.name]
    if field_name is None:
        assert _data_message(env) is None
        assert isinstance(env.get("typingMessage"), dict)
        return
    data = _data_message(env)
    assert data is not None and field_name in data, path.name


# The message-parser contract: the captured reaction family produces a Touch
# and an ordinary text-bearing message still parses. Observation-only events
# (polls, pins, group updates, moderation) produce no NormalizedMessage at
# all — they travel the `parse_observations` sibling, asserted below.
_SIGNAL_TOUCH = {"reaction_add.json", "reaction_orphan_target.json"}
_SIGNAL_MESSAGE = {"link_preview.json"}


@pytest.mark.parametrize("path", SIGNAL, ids=lambda p: p.name)
def test_signal_parse_outcome_matches_the_current_capture_contract(path: Path) -> None:
    msg = parse_event(_load(path)["payload"], SIGNAL_OWN)
    if path.name in _SIGNAL_TOUCH:
        assert msg is not None and msg.touch is not None
        assert msg.touch.kind == "reaction" and msg.touch.emoji == "👍"
        assert msg.conversation_id == "Z3JvdXA="
    elif path.name in _SIGNAL_MESSAGE:
        assert msg is not None and msg.body == "take a look at this"
    else:
        assert msg is None, (
            f"{path.name} unexpectedly parsed as a message — observations"
            " are `parse_observations`' lane, never utterances"
        )


# ── Signal typed observations — the real capture contract per fixture ──────

_ACCOUNT = "sig-acct"


def _signal_observations(name: str) -> tuple[Observation, ...]:
    parsed = parse_observations(
        _load(FIXTURES / "signal" / name)["payload"],
        own_account=SIGNAL_OWN, account_key=_ACCOUNT)
    return parsed.observations if parsed is not None else ()


def test_signal_poll_create_is_a_typed_definition() -> None:
    (obs,) = _signal_observations("poll_create.json")
    assert obs.object_kind == "poll" and obs.operation == "create"
    p = obs.payload
    assert isinstance(p, PollPayload)
    assert p.question == "Lunch on Saturday?"
    assert p.options == (("0", "Noodles"), ("1", "Dim sum"), ("2", "Skip it"))
    assert p.multi_select is False and p.anonymous is False
    assert p.state == "open"
    # The poll's own identity is the creating message's (author, sent_ts) —
    # the same pair a pollVote names it by.
    assert obs.object_ref == "poll:1700"
    assert obs.target == EditTarget(sent_ts=1700)
    assert obs.target_author_id == "+85212345678"


def test_signal_poll_vote_is_a_complete_voter_selection() -> None:
    (obs,) = _signal_observations("poll_vote.json")
    assert obs.object_kind == "poll" and obs.operation == "revise"
    p = obs.payload
    assert isinstance(p, PollPayload)
    # `optionIndexes` is the voter's WHOLE selection — a revise, never a
    # per-option delta; the voter is the envelope source, kept distinct
    # from the poll's author under `target_author_id`.
    assert p.voter_ref == "+85212345679"
    assert p.selected == ("1",)
    assert obs.object_ref == "poll:1700"
    assert obs.target == EditTarget(sent_ts=1700)
    assert obs.target_author_id == "+85212345679"


def test_signal_poll_vote_retract_is_an_empty_selection() -> None:
    (obs,) = _signal_observations("poll_vote_retract.json")
    assert obs.operation == "revise"
    p = obs.payload
    assert isinstance(p, PollPayload) and p.selected == ()
    assert p.voter_ref == "+85212345679"


def test_signal_poll_vote_missing_target_is_dropped() -> None:
    assert _signal_observations("poll_vote_missing_target.json") == ()


def test_signal_poll_terminate_closes_the_poll() -> None:
    (obs,) = _signal_observations("poll_terminate.json")
    assert obs.object_kind == "poll" and obs.operation == "revise"
    p = obs.payload
    assert isinstance(p, PollPayload) and p.state == "closed"
    assert p.close_ms == 1730
    assert obs.target == EditTarget(sent_ts=1700)
    assert obs.target_author_id == "+85212345678"


def test_signal_pins_carry_target_and_duration_gap() -> None:
    (add,) = _signal_observations("pin_message.json")
    assert add.object_kind == "pin" and add.operation == "add"
    p = add.payload
    assert isinstance(p, PinPayload)
    assert p.message == EditTarget(sent_ts=1600)
    assert add.target == EditTarget(sent_ts=1600)
    assert add.target_author_id == "+85212345679"
    # `pinDurationSeconds` is delivered but has no payload slot — an
    # explicit coverage gap, never a silently dropped field.
    assert "pin_duration_seconds" in add.coverage.gaps

    (rm,) = _signal_observations("unpin_message.json")
    assert rm.object_kind == "pin" and rm.operation == "remove"
    rp = rm.payload
    assert isinstance(rp, PinPayload)
    assert rp.message == EditTarget(sent_ts=1600)


def test_signal_admin_delete_is_moderation_not_a_delete_touch() -> None:
    (obs,) = _signal_observations("admin_delete.json")
    assert obs.object_kind == "room" and obs.operation == "revise"
    fields = obs.payload.fields  # type: ignore[attr-defined]
    fact = fields["moderation.admin_delete"]
    assert fact["target_sent_ts"] == 1600
    assert fact["target_author"] == "+85212345679"
    # The admin actor is the envelope source, kept distinct from the
    # deleted message's author — no author-only remoteDelete is forged.
    assert obs.actor_id == "+85212345678"
    assert obs.target_author_id == "+85212345679"


def test_signal_group_update_is_room_revision_only() -> None:
    (obs,) = _signal_observations("group_update.json")
    assert obs.object_kind == "room" and obs.operation == "revise"
    assert obs.payload.fields == {"name": "Example Group"}  # type: ignore[attr-defined]
    # The group's own revision is a comparable source position — the one
    # place Signal genuinely orders.
    assert obs.source_position == 41
    assert obs.revision_scope == "signal.group_revision"


def test_signal_contacts_and_previews_are_artifacts() -> None:
    (contact,) = _signal_observations("shared_contact.json")
    assert contact.object_kind == "artifact" and contact.operation == "create"
    assert contact.payload.artifact_kind == "contact"  # type: ignore[attr-defined]

    (preview,) = _signal_observations("link_preview.json")
    assert preview.object_kind == "artifact" and preview.operation == "create"
    assert preview.payload.artifact_kind == "link_preview"  # type: ignore[attr-defined]
    assert preview.payload.preview is True  # type: ignore[attr-defined]


def test_signal_admission_and_ephemeral_boundaries() -> None:
    """A group-less poll is the DM boundary; a typing ping is excluded
    ephemeral — both answer no observation rather than a forged one."""
    assert _signal_observations("poll_create_dm.json") == ()
    assert _signal_observations("typing_message.json") == ()
    # A bare reaction still travels the Touch lane — never BOTH paths for
    # one source fact.
    assert _signal_observations("reaction_add.json") == ()


def test_the_reaction_fixtures_keep_signal_target_addressing() -> None:
    """Signal names a message by (author, sent_ts): both reaction vectors must
    land on that addressing, including the one that will orphan at ingest."""
    doc = _load(FIXTURES / "signal" / "reaction_orphan_target.json")
    msg = parse_event(doc["payload"], SIGNAL_OWN)
    assert msg is not None and msg.touch is not None
    assert msg.touch.target == EditTarget(sent_ts=9999)
    assert msg.touch.target_author == "+85212345679"


def test_typing_is_dropped_and_stays_dropped() -> None:
    doc = _load(FIXTURES / "signal" / "typing_message.json")
    assert parse_event(doc["payload"], SIGNAL_OWN) is None


# ── Discord: hydrate the real SDK DTOs, then the real parsers ─────────────────


# Gateway `d` -> discord.py model the dispatch constructs (the (data,)-shaped
# raw models; reaction add/remove need PartialEmoji + the event-type literal
# exactly the way `ConnectionState.parse_message_reaction_add` builds them).
def _reaction_event(d: dict[str, Any], event_type: str) -> RawReactionActionEvent:
    return RawReactionActionEvent(d, PartialEmoji.from_dict(d["emoji"]), event_type)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("fixture", "build", "want"),
    [
        (
            "message_poll_vote_add.json",
            lambda d: RawPollVoteActionEvent(d),
            {"user_id", "answer_id", "message_id"},
        ),
        (
            "message_poll_vote_remove.json",
            lambda d: RawPollVoteActionEvent(d),
            {"user_id", "answer_id", "message_id"},
        ),
        ("poll_vote_add_dm.json", lambda d: RawPollVoteActionEvent(d), {"user_id", "answer_id"}),
        (
            "message_reaction_remove_all.json",
            lambda d: RawReactionClearEvent(d),
            {"message_id", "channel_id", "guild_id"},
        ),
        (
            "message_reaction_remove_emoji.json",
            lambda d: RawReactionClearEmojiEvent(d, PartialEmoji.from_dict(d["emoji"])),  # type: ignore[arg-type]
            {"message_id", "emoji"},
        ),
        (
            "message_delete.json",
            lambda d: RawMessageDeleteEvent(d),
            {"message_id", "channel_id", "guild_id"},
        ),
        (
            "thread_delete.json",
            lambda d: RawThreadDeleteEvent(d),
            {"thread_id", "parent_id", "guild_id"},
        ),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_the_gateway_payload_hydrates_the_pinned_sdk_model(
    fixture: str, build: Any, want: set[str]
) -> None:
    """A generated `d` the pinned `Raw*Event` cannot read would mean the
    fixture drifted from the real wire shape."""
    d = _load(FIXTURES / "discord" / fixture)["payload"]["d"]
    event = build(d)
    missing = {a for a in want if getattr(event, a, None) is None}
    assert not missing, f"{fixture}: SDK left {missing} empty"


def test_the_dm_poll_vote_hydrates_with_no_guild() -> None:
    """The SDK accepts the DM shape; the room-ref admission boundary — not the
    DTO — is where it stops."""
    d = _load(FIXTURES / "discord" / "poll_vote_add_dm.json")["payload"]["d"]
    event = RawPollVoteActionEvent(d)
    assert event.guild_id is None and event.answer_id == 1


@pytest.mark.parametrize(
    ("fixture", "removed"),
    [("message_reaction_add.json", False), ("message_reaction_remove.json", True)],
)
def test_reaction_fixtures_parse_through_the_real_touch_path(fixture: str, removed: bool) -> None:
    """The shipped baseline family, through the real SDK DTO and the real parser."""
    d = _load(FIXTURES / "discord" / fixture)["payload"]["d"]
    event = _reaction_event(d, "REACTION_REMOVE" if removed else "REACTION_ADD")
    msg = parse_discord_reaction(event, own_id=DISCORD_OWN, removed=removed)
    assert msg is not None and msg.conversation_id == "123456789012345600/123456789012345601"
    assert msg.touch is not None
    assert msg.touch == Touch(
        kind="reaction",
        target=EditTarget(message_id=d["message_id"]),
        emoji="🔥",
        removed=removed,
    )


def test_the_dm_reaction_is_dropped_at_admission() -> None:
    d = _load(FIXTURES / "discord" / "message_reaction_add_dm.json")["payload"]["d"]
    event = _reaction_event(d, "REACTION_ADD")
    assert parse_discord_reaction(event, own_id=DISCORD_OWN, removed=False) is None


def test_the_delete_fixture_parses_through_the_real_touch_path() -> None:
    d = _load(FIXTURES / "discord" / "message_delete.json")["payload"]["d"]
    msg = parse_discord_delete(RawMessageDeleteEvent(d))
    assert msg is not None and msg.touch is not None and msg.touch.kind == "delete"
    assert msg.touch.target == EditTarget(message_id=d["id"])
    assert msg.sender_id == "", "a delete names no actor"


# ── Discord message-carried shapes, hydrated onto the parser's Protocol ───────


@dataclass
class _FakeAttachment:
    id: int
    content_type: str | None = None
    filename: str = ""
    size: int = 0
    url: str = ""

    def is_voice_message(self) -> bool:
        return False


def _message_from_gateway(d: dict[str, Any]) -> Any:
    """A gateway MESSAGE_CREATE/UPDATE `d` reduced to the attributes
    `parse_discord_message`'s Protocol reads — the same structural fake the
    existing suite uses, filled from the fixture rather than hand-built."""
    guild = SimpleNamespace(id=int(d["guild_id"])) if d.get("guild_id") else None
    channel = SimpleNamespace(id=int(d["channel_id"]), parent_id=d.get("parent_id"))
    mentions = [
        SimpleNamespace(id=int(u["id"]), display_name=u.get("global_name") or u["username"])
        for u in d.get("mentions") or []
    ]
    attachments = [
        _FakeAttachment(
            id=int(a["id"]),
            content_type=a.get("content_type"),
            filename=a.get("filename") or "",
            size=a.get("size") or 0,
            url=a.get("url") or "",
        )
        for a in d.get("attachments") or []
    ]
    reference = None
    if d.get("message_reference"):
        mr = d["message_reference"]
        reference = SimpleNamespace(
            resolved=None,
            message_id=int(mr["message_id"]),
            channel_id=int(mr["channel_id"]),
            guild_id=int(mr["guild_id"]) if mr.get("guild_id") else None)
    poll = None
    if d.get("poll"):
        pd = d["poll"]
        results = pd.get("results") or {}
        poll = SimpleNamespace(
            question=SimpleNamespace(
                text=(pd.get("question") or {}).get("text")),
            answers=[
                SimpleNamespace(
                    answer_id=a.get("answer_id"),
                    poll_media=SimpleNamespace(
                        text=(a.get("poll_media") or {}).get("text")))
                for a in pd.get("answers") or []
            ],
            allow_multiselect=pd.get("allow_multiselect"),
            expires_at=(datetime.fromisoformat(pd["expiry"])
                        if pd.get("expiry") else None),
            results=(SimpleNamespace(
                is_finalized=results.get("is_finalized"),
                answer_counts=[
                    SimpleNamespace(id=a.get("id"), count=a.get("count"))
                    for a in results.get("answer_counts") or []
                ]) if results else None),
        )
    return SimpleNamespace(
        id=int(d["id"]),
        author=SimpleNamespace(
            id=int(d["author"]["id"]),
            display_name=d["author"].get("global_name") or d["author"]["username"],
        ),
        guild=guild,
        channel=channel,
        content=d.get("content") or "",
        mentions=mentions,
        attachments=attachments,
        reference=reference,
        stickers=[],
        embeds=d.get("embeds") or [],
        poll=poll,
        message_snapshots=d.get("message_snapshots") or [],
        type=SimpleNamespace(value=d.get("type", 0)),
        edited_at=None,
    )


def test_the_poll_message_parses_as_a_marker_and_loses_the_object() -> None:
    """The utterance still renders `[poll]` — the definition now travels as
    a typed `poll/create` sidecar on the same envelope, asserted next."""
    d = _load(FIXTURES / "discord" / "message_create_poll.json")["payload"]["d"]
    msg = parse_discord_message(_message_from_gateway(d), own_id=DISCORD_OWN)
    assert msg is not None and msg.body == "[poll]"
    assert msg.message_id == d["id"]


def test_discord_poll_definition_is_typed() -> None:
    d = _load(FIXTURES / "discord" / "message_create_poll.json")["payload"]["d"]
    obs = parse_discord_poll_definition(
        _message_from_gateway(d), own_id=DISCORD_OWN, account_key="acct")
    assert obs is not None
    assert obs.object_kind == "poll" and obs.operation == "create"
    assert obs.object_ref == f"poll:{d['id']}"
    p = obs.payload
    assert isinstance(p, PollPayload)
    assert p.question == "Lunch on Saturday?"
    assert p.options == (("1", "Noodles"), ("2", "Dim sum"), ("3", "Skip it"))
    assert p.multi_select is False and p.anonymous is False
    assert p.state == "open" and p.close_ms is not None
    assert obs.target == EditTarget(message_id=d["id"])
    assert obs.actor_id == d["author"]["id"]
    assert obs.scope.conversation_id == f"{d['guild_id']}/{d['channel_id']}"


def test_discord_poll_votes_are_per_option_deltas() -> None:
    for fixture, want_op in (("message_poll_vote_add.json", "add"),
                             ("message_poll_vote_remove.json", "remove")):
        d = _load(FIXTURES / "discord" / fixture)["payload"]["d"]
        obs = parse_discord_poll_vote(
            RawPollVoteActionEvent(d), own_id=DISCORD_OWN,
            account_key="acct", added=want_op == "add")
        assert obs is not None, fixture
        assert obs.object_kind == "poll" and obs.operation == want_op
        assert obs.object_ref == f"poll:{d['message_id']}"
        p = obs.payload
        assert isinstance(p, PollPayload)
        assert p.voter_ref == d["user_id"]
        assert p.option_ref == str(d["answer_id"])
        # The vote names its voter but the event itself carries no time —
        # unknown, never a fabricated one.
        assert obs.actor_id == d["user_id"] and obs.time_quality == "unknown"


def test_the_dm_poll_vote_is_dropped_at_admission() -> None:
    d = _load(FIXTURES / "discord" / "poll_vote_add_dm.json")["payload"]["d"]
    obs = parse_discord_poll_vote(
        RawPollVoteActionEvent(d), own_id=DISCORD_OWN,
        account_key="acct", added=True)
    assert obs is None


def test_discord_reaction_clears_are_typed_clears() -> None:
    d = _load(FIXTURES / "discord" / "message_reaction_remove_all.json")["payload"]["d"]
    obs = parse_discord_reaction_clear(RawReactionClearEvent(d), account_key="acct")
    assert obs is not None
    assert obs.object_kind == "reaction" and obs.operation == "clear"
    assert obs.object_ref == f"msg:{d['message_id']}"
    # Covered scope is the whole emoji set — empty means "all observed";
    # the moderator who cleared is never named.
    assert obs.payload.covered_emojis == ()  # type: ignore[attr-defined]
    assert obs.actor_status == "not_supplied"

    d = _load(FIXTURES / "discord" / "message_reaction_remove_emoji.json")["payload"]["d"]
    obs = parse_discord_reaction_clear(
        RawReactionClearEmojiEvent(d, PartialEmoji.from_dict(d["emoji"])),  # type: ignore[arg-type]
        account_key="acct", emoji="🔥")
    assert obs is not None
    assert obs.operation == "clear"
    assert obs.payload.covered_emojis == ("🔥",)  # type: ignore[attr-defined]


def test_the_forward_parses_as_a_marker_and_loses_the_snapshot() -> None:
    d = _load(FIXTURES / "discord" / "message_create_forward.json")["payload"]["d"]
    msg = parse_discord_message(_message_from_gateway(d), own_id=DISCORD_OWN)
    assert msg is not None and msg.body == "[forwarded message]"


def test_the_forward_carries_its_origin_as_metadata() -> None:
    d = _load(FIXTURES / "discord" / "message_create_forward.json")["payload"]["d"]
    obs = parse_discord_message_metadata(
        _message_from_gateway(d), account_key="acct")
    assert obs is not None
    assert obs.object_kind == "message_metadata" and obs.operation == "create"
    origin = obs.payload.forward_origin  # type: ignore[attr-defined]
    assert origin == (
        f"discord:{d['message_reference']['guild_id']}"
        f"/{d['message_reference']['channel_id']}"
        f"/{d['message_reference']['message_id']}")


def test_the_pin_system_message_is_dropped_unread() -> None:
    """A type-6 CHANNEL_PINNED_MESSAGE carries empty `content` and no media —
    the body rules still answer no utterance. The pin evidence now travels
    the observation lane, asserted next."""
    d = _load(FIXTURES / "discord" / "message_create_pinned.json")["payload"]["d"]
    assert d["type"] == 6 and d["message_reference"]["message_id"]
    assert parse_discord_message(_message_from_gateway(d), own_id=DISCORD_OWN) is None


def test_the_pin_system_message_is_real_pin_evidence() -> None:
    d = _load(FIXTURES / "discord" / "message_create_pinned.json")["payload"]["d"]
    obs = parse_discord_pin_message(
        _message_from_gateway(d), own_id=DISCORD_OWN, account_key="acct")
    assert obs is not None
    assert obs.object_kind == "pin" and obs.operation == "add"
    p = obs.payload
    assert isinstance(p, PinPayload)
    assert p.message == EditTarget(message_id=d["message_reference"]["message_id"])
    assert obs.target == EditTarget(message_id=d["message_reference"]["message_id"])
    # The notice's author IS the pinner — real pin evidence, not a guess.
    assert obs.actor_id == d["author"]["id"]


def test_channel_pins_update_is_an_incomplete_marker() -> None:
    """The event names no pinned message and no actor — the coverage gaps
    name exactly what it cannot say; the type-6 system message (or a later
    read) is the real target evidence."""
    d = _load(FIXTURES / "discord" / "channel_pins_update.json")["payload"]["d"]
    channel = SimpleNamespace(
        id=int(d["channel_id"]), parent_id=None,
        guild=SimpleNamespace(id=int(d["guild_id"])))
    obs = parse_discord_pins_update(
        channel, d["last_pin_timestamp"], account_key="acct")
    assert obs is not None
    assert obs.object_kind == "pin" and obs.operation == "snapshot"
    assert obs.coverage.complete is False
    assert set(obs.coverage.gaps) == {"targets", "actor"}
    assert obs.actor_status == "not_supplied"


def test_the_poll_close_is_not_an_edit_and_is_dropped() -> None:
    """A finalized-poll MESSAGE_UPDATE still parses as no edit — the close
    is captured on `parse_discord_poll_close` (asserted next), which keys
    on `results.is_finalized`, never on `edited_timestamp`."""
    d = _load(FIXTURES / "discord" / "message_update_poll_finalized.json")["payload"]["d"]
    assert d["edited_timestamp"] is None and d["poll"]["results"]["is_finalized"] is True
    assert parse_discord_edit(_message_from_gateway(d), own_id=DISCORD_OWN) is None


def test_the_poll_close_produces_state_and_counts() -> None:
    d = _load(FIXTURES / "discord" / "message_update_poll_finalized.json")["payload"]["d"]
    obs = parse_discord_poll_close(
        _message_from_gateway(d), account_key="acct")
    assert len(obs) == 2
    state, counts = obs
    assert state.operation == "revise" and counts.operation == "snapshot"
    assert isinstance(state.payload, PollPayload)
    assert state.payload.state == "closed"
    assert dict(counts.payload.option_counts) == {"1": 3, "2": 2, "3": 0}  # type: ignore[attr-defined]
    # An unfinalized update answers nothing.
    obs = parse_discord_poll_close(
        _message_from_gateway(
            _load(FIXTURES / "discord" / "message_create_poll.json")["payload"]["d"]),
        account_key="acct")
    assert obs == ()


# ── Discord channel/thread/member/role lifecycle observations ─────────────


def test_thread_lifecycle_observations() -> None:
    d = _load(FIXTURES / "discord" / "thread_create.json")["payload"]["d"]
    thread = SimpleNamespace(
        id=int(d["id"]), name=d["name"], parent_id=int(d["parent_id"]),
        owner_id=int(d["owner_id"]), archived=False, locked=False,
        auto_archive_duration=1440, slowmode_delay=0,
        type=SimpleNamespace(value=d["type"]),
        guild=SimpleNamespace(id=int(d["guild_id"])))
    obs = parse_discord_thread_create(thread, account_key="acct")
    assert obs is not None
    assert obs.object_kind == "room" and obs.operation == "create"
    assert obs.object_ref == f"thread:{d['id']}"
    assert obs.scope.conversation_id == f"{d['guild_id']}/{d['parent_id']}"
    assert obs.scope.thread == d["id"]
    assert obs.actor_id == d["owner_id"]  # creator, not the bot join in `member`

    d = _load(FIXTURES / "discord" / "thread_update.json")["payload"]["d"]
    obs = parse_discord_thread_update(d, account_key="acct")
    assert obs is not None and obs.operation == "snapshot"
    assert obs.payload.fields["archived"] is True  # type: ignore[attr-defined]
    # An AFTER snapshot reports covered fields — never an invented delta.
    assert obs.actor_status == "not_supplied"

    d = _load(FIXTURES / "discord" / "thread_delete.json")["payload"]["d"]
    obs = parse_discord_thread_delete(RawThreadDeleteEvent(d), account_key="acct")
    assert obs is not None and obs.operation == "withdraw"
    assert obs.actor_status == "not_supplied"


def test_channel_update_is_an_after_snapshot() -> None:
    d = _load(FIXTURES / "discord" / "channel_update.json")["payload"]["d"]
    ow = SimpleNamespace(pair=lambda: (
        SimpleNamespace(value=1024), SimpleNamespace(value=0)))
    channel = SimpleNamespace(
        id=int(d["id"]), name=d["name"], topic=d["topic"],
        position=d["position"], parent_id=int(d["parent_id"]),
        nsfw=d["nsfw"], slowmode_delay=d["rate_limit_per_user"],
        type=SimpleNamespace(value=d["type"]),
        permission_overwrites={int(d["permission_overwrites"][0]["id"]): ow},
        guild=SimpleNamespace(id=int(d["guild_id"])))
    obs = parse_discord_channel_update(channel, account_key="acct")
    assert obs is not None
    assert obs.object_kind == "room" and obs.operation == "snapshot"
    assert obs.object_ref == f"channel:{d['id']}"
    fields = obs.payload.fields  # type: ignore[attr-defined]
    assert fields["name"] == "general-east"
    assert fields["topic"] == "new fixture topic"
    assert fields["permission_overwrites"] == {
        "123456789012345900": {"allow": 1024, "deny": 0}}


def test_guild_member_events_are_guild_scope() -> None:
    d = _load(FIXTURES / "discord" / "guild_member_add.json")["payload"]["d"]
    member = SimpleNamespace(
        id=int(d["user"]["id"]), guild=SimpleNamespace(id=int(d["guild_id"])),
        roles=[], joined_at=None)
    obs = parse_discord_member_add(member, account_key="acct")
    assert obs is not None
    assert obs.object_kind == "membership" and obs.operation == "add"
    # The OBJECT is the guild's member collection — a guild roster is never
    # a channel's audience.
    assert obs.object_ref == f"guild:{d['guild_id']}"
    assert obs.scope.conversation_id == d["guild_id"]
    assert obs.subject_ref == d["user"]["id"]
    assert obs.actor_id == d["user"]["id"]

    d = _load(FIXTURES / "discord" / "guild_member_remove.json")["payload"]["d"]
    raw = RawMemberRemoveEvent(d, SimpleNamespace(id=int(d["user"]["id"])))
    obs = parse_discord_member_remove(raw, account_key="acct")
    assert obs is not None and obs.operation == "remove"
    assert obs.subject_ref == d["user"]["id"]
    assert obs.payload.state == "left"  # type: ignore[attr-defined]
    assert obs.actor_status == "not_supplied"

    d = _load(FIXTURES / "discord" / "guild_member_update.json")["payload"]["d"]
    member = SimpleNamespace(
        id=int(d["user"]["id"]), guild=SimpleNamespace(id=int(d["guild_id"])),
        roles=[SimpleNamespace(id=int(r)) for r in d["roles"]])
    obs = parse_discord_member_update(member, account_key="acct")
    assert obs is not None and obs.operation == "revise"
    assert isinstance(obs.payload, MembershipPayload)
    assert obs.payload.role == "123456789012345900,123456789012345901"


def test_guild_role_update_is_a_guild_snapshot() -> None:
    d = _load(FIXTURES / "discord" / "guild_role_update.json")["payload"]["d"]
    r = d["role"]
    role = SimpleNamespace(
        id=int(r["id"]), name=r["name"],
        permissions=SimpleNamespace(value=int(r["permissions"])),
        position=r["position"], color=SimpleNamespace(value=r["color"]),
        hoist=r["hoist"], mentionable=r["mentionable"], managed=r["managed"],
        guild=SimpleNamespace(id=int(d["guild_id"])))
    obs = parse_discord_role_update(role, account_key="acct")
    assert obs is not None
    assert obs.object_kind == "room" and obs.operation == "snapshot"
    assert obs.object_ref == f"role:{r['id']}"
    assert obs.scope.conversation_id == d["guild_id"]
    fields = obs.payload.fields  # type: ignore[attr-defined]
    assert fields["name"] == "fixture-role"
    assert fields["permissions"] == 1043200
