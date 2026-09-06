"""The engagement gate that ships now.

`TriggerTextPolicy` is deterministic and does no I/O, so it cannot fail — which
is why spec §4.3's failure table has no row for stage 5. `EngagePolicy` is
nonetheless `async` because the `ClassifierPolicy` that joins it later is a
network call, and a sync-to-async change would break every call site on the
ingest pipeline.

When the classifier lands it is `CompositePolicy([TriggerTextPolicy,
ClassifierPolicy])` — first-match-wins, deterministic trigger first and
short-circuiting — NOT a replacement. Replacing this gate would mean an LLM
outage silently stops explicit `nora.000` tags from working, which under R-5 is
strictly worse than today.
"""

from __future__ import annotations

from nora.domain.models import NormalizedMessage
from nora.domain.ports import EngageDecision, EngagePolicy
from nora.policy import Policy
from nora.tag import is_tagged


class TriggerTextPolicy:
    """Satisfies `nora.domain.ports.EngagePolicy`."""

    def __init__(self, trigger_text: str) -> None:
        self._trigger_text = trigger_text

    async def should_engage(self, *, message: NormalizedMessage) -> EngageDecision:
        # R-11's matching rules (word boundary, code spans, Unicode
        # normalisation) live in `nora.tag.is_tagged` and are pinned by
        # `test_trigger_matrix`. Reimplementing them here would create a second
        # implementation free to drift from the one the rule is tested against.
        if is_tagged(message.body, self._trigger_text):
            return EngageDecision(engage=True, reason="trigger_text")
        return EngageDecision(engage=False, reason="no_trigger")


class MentionPolicy:
    """Engage when the sender @-mentioned Nora. Satisfies `EngagePolicy`.

    This is the trigger an operator reaches for first, and until now it could
    never fire: a Signal mention puts U+FFFC in the body and the mentioned ids
    in a parallel array, so `is_tagged` — a substring match — cannot see it.
    Tagging her the obvious way produced ingest, attribution, and silence.

    Matching is against Nora's OWN identifier set, the same one R-12 uses to
    refuse self-tags (`nora.platform.signal.self_id`). That set only became
    trustworthy when alias derivation was fixed (DEC-19); before that it was
    `{primary}`, so a mention carrying only her ACI — which is what Signal
    actually sends — would not have matched.

    It is a stronger signal than trigger text, not a weaker one. R-11 exists to
    separate tagging INTENT from string presence (a trigger quoted as code is
    not a tag); a structural mention cannot be quoted, smuggled through
    zero-width characters, or produced by accident.
    """

    def __init__(self, self_ids: frozenset[str]) -> None:
        self._self_ids = self_ids

    async def should_engage(self, *, message: NormalizedMessage) -> EngageDecision:
        if self._self_ids and (message.mentions & self._self_ids):
            return EngageDecision(engage=True, reason="mention")
        return EngageDecision(engage=False, reason="no_trigger")


class ReplyToNoraPolicy:
    """Engage when the sender replied to one of Nora's own messages.

    Replying to somebody is how you address them in a group chat — arguably
    more directly than @-mentioning, since it points at a specific thing they
    said. Without this, answering Nora meant either typing the trigger text or
    tagging her again, which is not how anyone talks.

    Signal carries the reply as `dataMessage.quote` with the quoted author, so
    this matches that author against Nora's OWN identifier set — the same set
    R-12 uses to refuse self-tags, and the same one `MentionPolicy` uses. It
    became trustworthy only when DEC-19 fixed alias derivation: the quote
    carries her ACI, and before that the set was `{primary}`.

    Matched on the author's ID, never their display name (design/10 §1.3) — a
    profile name is settable by anyone, so matching it would let a member
    impersonate Nora and trigger her by quoting themselves.
    """

    def __init__(self, self_ids: frozenset[str]) -> None:
        self._self_ids = self_ids

    async def should_engage(self, *, message: NormalizedMessage) -> EngageDecision:
        quote = message.quote
        if quote and self._self_ids and quote.author_id in self._self_ids:
            return EngageDecision(engage=True, reason="reply_to_nora")
        return EngageDecision(engage=False, reason="no_trigger")


class VoiceNotePolicy:
    """Engage when the message carried a voice note (operator directive 2026-08-26).

    Miles: 「總之任何 Voice Note 都會直入上 Nora 度先囉」 — a voice note is
    conversation content whose transcription the ingest pipeline already put in
    the body (`[voice note #hex: …]`), so any voice note addressed to the group
    is addressed to Nora too: she reads it, and replies when the words warrant
    it. This policy exists so that engagement does not depend on the
    transcription succeeding or on the speaker happening to say her name — the
    note engages her by being a voice note.

    It is gated on the parser's `voice_note` flag (`MediaRef.voice_note`),
    NOT on the rendered body containing a transcript: a failed transcription
    still leaves the handle and the note is still content she should see
    (R-27 — never invent words, but never drop the note either).
    """

    async def should_engage(self, *, message: NormalizedMessage) -> EngageDecision:
        if any(ref.voice_note for ref in message.media):
            return EngageDecision(engage=True, reason="voice_note")
        return EngageDecision(engage=False, reason="no_trigger")


class RelevanceGatePolicy:
    """The Relevance Gate's ROOM gate (design/dreamer-agent-loop/36 §1).

    Never engages: an untagged message in a room the operator opted in
    (`relevance_gate = true` on its `[[conversations]]` entry) comes back
    `relevance_gate=True, engage=False` — a CANDIDATE for the quiescence
    window, not an admission and not a verdict. The dispatcher notes it with
    `ParticipationTrigger`, which owns the window; when the window closes the
    Relevance Gate decides whether the burst is worth a turn at all, and only
    a GO reaches `FirePath.speak`. Nothing about the tag path changes here.

    This class answers "is this ROOM allowed to be considered", never "is
    this MESSAGE worth answering" — that second question is the gate's, one
    model call later, and it is the whole reason the two are separate.

    Wired LAST in the composite and only when `[relevance_gate] enabled` — so
    a real tag keeps its own reason, and a deployment with the gate off has
    exactly the gate it always had. The room lookup is against policy.toml's
    allowlist (`Policy.conversation`), not config, because "may answer when
    tagged" vs "may speak uncalled" is a trust distinction and trust lives in
    the policy file.
    """

    def __init__(self, allowlist: Policy) -> None:
        self._allowlist = allowlist

    async def should_engage(self, *, message: NormalizedMessage) -> EngageDecision:
        convo = self._allowlist.conversation(message.platform, message.conversation_id)
        if convo is not None and convo.relevance_gate:
            return EngageDecision(
                engage=False, reason="relevance_gate_room", relevance_gate=True,
            )
        return EngageDecision(engage=False, reason="no_trigger")


class CompositePolicy:
    """First match wins. Satisfies `EngagePolicy`.

    The shape this module's docstring already anticipated for the classifier:
    deterministic policies first, short-circuiting, so a later failing or
    slower policy can never stop an explicit tag from working.

    A `relevance_gate` verdict short-circuits the same way an engage verdict
    does — it is a claim about the message a later policy must not overwrite
    with `no_trigger` — but it is still `engage=False`: nothing downstream may
    read it as an admission to the tag path.
    """

    def __init__(self, policies: tuple[EngagePolicy, ...]) -> None:
        self._policies = policies

    async def should_engage(self, *, message: NormalizedMessage) -> EngageDecision:
        for policy in self._policies:
            decision = await policy.should_engage(message=message)
            if decision.engage or decision.relevance_gate:
                return decision
        return EngageDecision(engage=False, reason="no_trigger")
