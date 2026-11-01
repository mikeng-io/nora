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
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import discord
import pytest
from discord.partial_emoji import PartialEmoji
from discord.raw_models import (
    RawMessageDeleteEvent,
    RawPollVoteActionEvent,
    RawReactionActionEvent,
    RawReactionClearEmojiEvent,
    RawReactionClearEvent,
    RawThreadDeleteEvent,
)
from discord.state import ConnectionState

from nora.domain.models import EditTarget, Touch
from nora.platform.discord.parser import (
    parse_discord_delete,
    parse_discord_edit,
    parse_discord_message,
    parse_discord_reaction,
)
from nora.platform.signal.parser import _data_message, extract_envelope, parse_event

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


# The parser contract TODAY: the captured reaction family produces a Touch,
# an ordinary text-bearing message still parses (its unhandled sibling fields
# are dropped, not the row), and every unhandled family is dropped whole.
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
            f"{path.name} unexpectedly parsed — update this pin with the capture lane"
        )


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
    reference = SimpleNamespace(resolved=None) if d.get("message_reference") else None
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
        poll=SimpleNamespace(**d["poll"]) if d.get("poll") else None,
        message_snapshots=d.get("message_snapshots") or [],
        edited_at=None,
    )


def test_the_poll_message_parses_as_a_marker_and_loses_the_object() -> None:
    """Today's contract: the turn is visible (`[poll]`), the definition,
    options, tally and expiry are gone — exactly the gap this cluster exists
    to close."""
    d = _load(FIXTURES / "discord" / "message_create_poll.json")["payload"]["d"]
    msg = parse_discord_message(_message_from_gateway(d), own_id=DISCORD_OWN)
    assert msg is not None and msg.body == "[poll]"
    assert msg.message_id == d["id"]


def test_the_forward_parses_as_a_marker_and_loses_the_snapshot() -> None:
    d = _load(FIXTURES / "discord" / "message_create_forward.json")["payload"]["d"]
    msg = parse_discord_message(_message_from_gateway(d), own_id=DISCORD_OWN)
    assert msg is not None and msg.body == "[forwarded message]"


def test_the_pin_system_message_is_dropped_unread() -> None:
    """A type-6 CHANNEL_PINNED_MESSAGE carries empty `content` and no media —
    under today's body rules it parses as nothing at all, so the ONLY
    per-message pin evidence Discord sends never reaches the record."""
    d = _load(FIXTURES / "discord" / "message_create_pinned.json")["payload"]["d"]
    assert d["type"] == 6 and d["message_reference"]["message_id"]
    assert parse_discord_message(_message_from_gateway(d), own_id=DISCORD_OWN) is None


def test_the_poll_close_is_not_an_edit_and_is_dropped() -> None:
    """Discord delivers poll close as a MESSAGE_UPDATE with finalized results
    and `edited_timestamp` null — `parse_discord_edit` answers None by design,
    which today means the close event vanishes on the edit seam."""
    d = _load(FIXTURES / "discord" / "message_update_poll_finalized.json")["payload"]["d"]
    assert d["edited_timestamp"] is None and d["poll"]["results"]["is_finalized"] is True
    assert parse_discord_edit(_message_from_gateway(d), own_id=DISCORD_OWN) is None
