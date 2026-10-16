import pytest

from nora.tag import is_tagged

TRIGGER = "nora.000"

MATRIX = [
    # (body, fires, why)
    ("nora.000 what time", True, "plain trigger"),
    ("NORA.000", True, "case-insensitive"),
    ("hey nora.000!", True, "punctuation boundary"),
    ("nora.000，你在嗎", True, "CJK punctuation boundary"),
    ("nora.0001", False, "trailing word char"),
    ("xnora.000", False, "leading word char"),
    ("nora_520", False, "not the trigger"),
    ("nora .520", False, "space inside the token"),
    ("nor a.520", False, "space inside the token"),
    ("`nora.000`", False, "inline code span"),
    ("```\nnora.000\n```", False, "fenced code block"),
    ("look: ```nora.000``` in code", False, "inline triple-backtick span"),
    ("n​ora.000", True, "zero-width space stripped"),
    ("nora​.000", True, "zero-width space stripped"),
    ("‮nora.000", True, "bidi control stripped"),
    ("> nora.000", True, "a quote is still the sender's own body"),
    ("", False, "empty body"),
    ("just talking about nora", False, "name without the token"),
    ("nora.000 and nora.000 again", True, "multiple occurrences still fire once"),
    ("ask nora.000﻿ now", True, "BOM stripped"),
]


@pytest.mark.parametrize("body,fires,why", MATRIX, ids=[m[2] for m in MATRIX])
def test_trigger_matrix(body, fires, why):
    assert is_tagged(body, TRIGGER) is fires, why


def test_trigger_is_configurable_without_a_code_change():
    assert is_tagged("hey @bot!", "@bot") is True
    assert is_tagged("hey @bot!", "nora.000") is False


def test_regex_metacharacters_in_the_trigger_are_escaped():
    assert is_tagged("call n.ra now", "n.ra") is True
    assert is_tagged("call nXra now", "n.ra") is False


def test_code_span_stripping_leaves_surrounding_text_taggable():
    assert is_tagged("`code` nora.000 real", TRIGGER) is True


def test_unclosed_fence_swallows_the_rest():
    """An unterminated fence is treated as code to the end — a conservative miss."""
    assert is_tagged("```\nnora.000", TRIGGER) is False


# ── @-mentions (the trigger an operator reaches for first) ──────────────────


def _mention_msg(mentions, body="￼ who are u"):
    from nora.domain.models import NormalizedMessage
    return NormalizedMessage(
        "signal", "g1", "group", "+8521", "Miles", 1, body, {},
        mentions=frozenset(mentions),
    )


NORA_ACI = "a1000000-0000-4000-8000-000000000000"


async def test_an_at_mention_of_nora_engages():
    """The real envelope from the live group. Signal puts U+FFFC in the body
    and the mentioned ids in a parallel array, so `is_tagged` cannot see the
    mention in the text alone, so a tag the obvious way would be recorded and
    never answered."""
    from nora.ingest.policy import MentionPolicy
    policy = MentionPolicy(frozenset({"+85290000100", NORA_ACI}))
    d = await policy.should_engage(message=_mention_msg({NORA_ACI}))
    assert d.engage is True
    assert d.reason == "mention"


async def test_a_mention_of_somebody_else_does_not_engage():
    from nora.ingest.policy import MentionPolicy
    policy = MentionPolicy(frozenset({NORA_ACI}))
    d = await policy.should_engage(message=_mention_msg({"someone-else-uuid"}))
    assert d.engage is False


async def test_no_mentions_at_all_does_not_engage():
    from nora.ingest.policy import MentionPolicy
    policy = MentionPolicy(frozenset({NORA_ACI}))
    assert (await policy.should_engage(message=_mention_msg(set(), "hello"))).engage is False


async def test_an_empty_self_id_set_never_engages():
    """Degraded identity (an empty self-id set) must not make her
    answer everything."""
    from nora.ingest.policy import MentionPolicy
    policy = MentionPolicy(frozenset())
    assert (await policy.should_engage(message=_mention_msg({NORA_ACI}))).engage is False


async def test_trigger_text_still_wins_first_in_the_composite():
    from nora.ingest.policy import CompositePolicy, MentionPolicy, TriggerTextPolicy
    p = CompositePolicy((TriggerTextPolicy("nora.000"), MentionPolicy(frozenset({NORA_ACI}))))
    text = await p.should_engage(message=_mention_msg(set(), "nora.000 hi"))
    assert text.engage is True and text.reason == "trigger_text"
    men = await p.should_engage(message=_mention_msg({NORA_ACI}))
    assert men.engage is True and men.reason == "mention"
    neither = await p.should_engage(message=_mention_msg(set(), "just chatting"))
    assert neither.engage is False


# ── replying to Nora is a trigger ───────────────────────────────────────────


def _reply_msg(author_id, text="早晨", quoted_ts=1785950108291):
    from nora.domain.models import NormalizedMessage, Quote
    return NormalizedMessage("signal","g1","group","+8521","Miles",1,"咁又係",{},
                             quote=Quote(author_id, text, quoted_ts))


async def test_replying_to_noras_message_engages():
    """Replying is how you address someone in a group chat — arguably more
    directly than @-mentioning, since it points at a specific thing they said.
    Without this, answering her would mean typing the trigger text again."""
    from nora.ingest.policy import ReplyToNoraPolicy
    p = ReplyToNoraPolicy(frozenset({NORA_ACI, "+85290000100"}))
    d = await p.should_engage(message=_reply_msg(NORA_ACI))
    assert d.engage is True and d.reason == "reply_to_nora"


async def test_replying_to_somebody_else_does_not_engage():
    from nora.ingest.policy import ReplyToNoraPolicy
    p = ReplyToNoraPolicy(frozenset({NORA_ACI}))
    d = await p.should_engage(message=_reply_msg("tylers-uuid", "食飯未"))
    assert d.engage is False


async def test_a_spoofed_display_name_cannot_trigger_her():
    """Matched on the author's ID, never their name — and there is no name on
    the wire to match even if someone wanted to: signal-cli's quote payload has
    no `authorName` field at all, so `Quote` carries none. A member calling
    themselves ทᴏʀɑ carries their own id and is refused on that."""
    from dataclasses import fields

    from nora.domain.models import Quote
    from nora.ingest.policy import ReplyToNoraPolicy
    assert "author_name" not in {f.name for f in fields(Quote)}
    p = ReplyToNoraPolicy(frozenset({NORA_ACI}))
    d = await p.should_engage(message=_reply_msg("impostor-uuid"))
    assert d.engage is False


async def test_a_degraded_self_id_set_never_engages_on_a_reply():
    from nora.ingest.policy import ReplyToNoraPolicy
    p = ReplyToNoraPolicy(frozenset())
    assert (await p.should_engage(message=_reply_msg(NORA_ACI, "x"))).engage is False


# ── a voice note engages her by being a voice note ──────────────────────────


def _voice_msg(voice=True, body="[voice note #ab12: 餵你今日得唔得閒]"):
    from nora.domain.media import MediaRef
    from nora.domain.models import NormalizedMessage
    return NormalizedMessage(
        "signal", "g1", "group", "+8521", "Miles", 1, body, {},
        media=(MediaRef(platform_id="a1", media_type="audio/aac",
                        filename="signal-2026-08-26-10-00-00-000.m4a",
                        voice_note=voice),),
    )


async def test_any_voice_note_engages():
    """Operator directive: 總之任何 Voice Note 都會直入上 Nora 度先囉.
    The note is conversation content whether or not the transcript succeeded —
    the handle alone must engage her (R-27: never invent words, never drop)."""
    from nora.ingest.policy import VoiceNotePolicy
    p = VoiceNotePolicy()
    d = await p.should_engage(message=_voice_msg())
    assert d.engage is True and d.reason == "voice_note"


async def test_a_failed_transcription_still_engages():
    from nora.ingest.policy import VoiceNotePolicy
    p = VoiceNotePolicy()
    d = await p.should_engage(message=_voice_msg(body="[voice note #ab12]"))
    assert d.engage is True and d.reason == "voice_note"


async def test_an_audio_file_that_is_not_a_voice_note_does_not_engage():
    """A shared song is an audio FILE, not conversation content addressed to
    her — the parser's `voice_note` flag is what decides, not the MIME type."""
    from nora.ingest.policy import VoiceNotePolicy
    p = VoiceNotePolicy()
    d = await p.should_engage(message=_voice_msg(voice=False, body="[audio #cd34]"))
    assert d.engage is False


async def test_a_plain_text_message_does_not_engage_on_voice_note_grounds():
    from nora.ingest.policy import VoiceNotePolicy
    p = VoiceNotePolicy()
    from nora.domain.models import NormalizedMessage
    m = NormalizedMessage("signal", "g1", "group", "+8521", "Miles", 1, "食飯未", {})
    assert (await p.should_engage(message=m)).engage is False


async def test_voice_note_engages_in_the_composite_without_a_trigger_word():
    """Even with trigger text matching nothing, a voice note alone engages —
    the pipeline gates on the flag, and the transcript (when present) rides
    in the body for her to read."""
    from nora.domain.media import MediaRef
    from nora.domain.models import NormalizedMessage
    from nora.ingest.policy import (
        CompositePolicy,
        MentionPolicy,
        ReplyToNoraPolicy,
        TriggerTextPolicy,
        VoiceNotePolicy,
    )
    p = CompositePolicy((
        TriggerTextPolicy("nora"),
        MentionPolicy(frozenset()),
        ReplyToNoraPolicy(frozenset()),
        VoiceNotePolicy(),
    ))
    m = NormalizedMessage(
        "signal", "g1", "group", "+8521", "Miles", 1,
        "[voice note #ab12: 餵你今日得唔得閒]", {},
        media=(MediaRef(platform_id="a1", media_type="audio/aac",
                        filename="signal-2026-08-26-10-00-00-000.m4a",
                        voice_note=True),),
    )
    d = await p.should_engage(message=m)
    assert d.engage is True and d.reason == "voice_note"
