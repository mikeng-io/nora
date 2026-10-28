"""Discord REST send: `POST /channels/{id}/messages`.

Mirrors `nora.platform.signal.send.SignalSender` deliberately — same session
construction, same status handling, same "log and return, never raise"
contract — for the same reason that module gives: a reader who has read one
should not have to re-read the other.

`styles`/`preview` are the port's DECORATION parameters
(`nora.domain.ports.PlatformAdapter.send`'s own docstring: "a platform with
no mention concept ignores them") and are unused here — Discord expresses
rich text inline in `content` itself (markdown), a different mechanism from
Signal's out-of-band style ranges.

**`mentions` IS honoured, and it is populated upstream, never by the
model.** `send_reply(text: str)` has no mention parameter for the model
to fill (`nora/agent/tools.py`) — it writes plaintext `@Name`.
`nora.agent.outbound.OutboundComposer` (platform-aware: a Discord snowflake is a
preferred recipient form, as are Signal's ACI/E164) resolves it against
`identity.toml` AND this room's live roster and hands `send` a `Mention` —
`(start, length, recipient)` in UTF-16 code units, the same contract Signal's
`bodyRange` uses. `nora.platform.discord.mention_resolve.
splice_composed_mentions` is what turns THAT into a real `<@id>` ping here:
converts the UTF-16 offsets to Python string indices
(`nora.domain.utf16.utf16_index`) and splices, right-to-left so an earlier
mention's offset survives a later one's splice.

`nora.platform.discord.mention_resolve.resolve_outbound_mentions` is the
name-matching fallback path, run only over whatever `@Name`
the composer's `Mention`s did NOT already cover (a handle unmapped in
`identity.toml`, or mapped but not confirmed in THIS room's roster): it looks
up the target guild's live member list and splices any exact `@<display name>`
match, server-side, right before the POST. A handle neither path resolves goes
out exactly as the model wrote it — never guessed. See that module's
docstring for the full reasoning and the fail-closed contract on both paths.

**A spliced `<@id>` chip is never handed to that second pass** —
`<@100000000000000002>` contains an `@` itself, so a
naive `"@" in resolved_text` check after the splice would (a) fire a live
member-list fetch on every composed-mention send even when nothing is left
to resolve, and (b) let an all-digit guild nickname `str.replace` a match
INSIDE an already-spliced id. `_SPLICED_MENTION_RE.split` below keeps every
chip out of both the "is there more to resolve" check and the replacement
itself — see the comment at the call site.

**`attachments` is the payload and IS supported: a multipart upload.**
When `attachments` are present, the message is sent as a Discord multipart
form — `payload_json` carries the message fields, `files[i]` carries each
attachment's bytes — one request, Discord's documented shape. Attachments are
never silently dropped: answering `ok=True` without them would have the audit
row, the model, the ✅ and `nora_replies_total{status="sent"}` all assert a
delivery the channel never received.
"""

from __future__ import annotations

import json
import logging
import re

import aiohttp

from ...domain.models import ConversationTarget
from ...domain.ports import (
    LinkPreview,
    Mention,
    OutboundAttachment,
    OutboundQuote,
    SendResult,
    TextStyle,
)
from .mention_resolve import (
    fetch_named_members,
    resolve_outbound_mentions,
    splice_composed_mentions,
)
from .snowflake import timestamp_ms

log = logging.getLogger("nora.discord.send")

DEFAULT_API_BASE = "https://discord.com/api/v10"

# A chip `splice_composed_mentions` just wrote. `re.split` with a CAPTURING
# group keeps every chip in the result (at the odd indices) so the name
# fallback below can skip them by construction rather than by a second
# "does this look like a chip" check — see the comment at the call site for
# why a chip must never reach `resolve_outbound_mentions`.
_SPLICED_MENTION_RE = re.compile(r"(<@\d+>)")


class DiscordSender:
    def __init__(
        self, token: str, *, base_url: str = DEFAULT_API_BASE, timeout: float = 10.0
    ) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def send(
        self,
        target: ConversationTarget,
        text: str,
        *,
        mentions: tuple[Mention, ...] = (),
        styles: tuple[TextStyle, ...] = (),
        preview: LinkPreview | None = None,
        attachments: tuple[OutboundAttachment, ...] = (),
        quote: OutboundQuote | None = None,
        request_id: str = "",
    ) -> SendResult:
        if target.conversation_type != "group":
            log.error(
                "refusing to send to conversation_type=%r (v1 is guild channels only)",
                target.conversation_type,
            )
            return SendResult(ok=False)
        # conversation_id is "<guild_id>/<channel_id>" (policy.example.toml);
        # the REST endpoint below addresses the channel alone. The guild half
        # is only needed below, for the member lookup a '@Name' in the text
        # requires. `target.thread` overrides the POST's channel: a thread is
        # a channel to Discord, and a reply inside one goes to its id while
        # the ROOM (and every policy answer) stays the parent's.
        guild_id, _, channel_id = target.conversation_id.partition("/")
        if not channel_id:
            # A conversation_id with no `/` partitions to an EMPTY channel, and
            # the POST below would address `/channels//messages` — a URL whose
            # own route does not exist, which Discord answers 405. That is a
            # rejection about a malformed request, not about this message, and
            # a caller that read it as one would misreport every send as a
            # guard refusal.
            #
            # Refused HERE, before the request, for the reason the
            # `conversation_type` guard above is: a send that cannot be
            # addressed is a wiring fault, and one named on its own terms is
            # worth more than an HTTP status an operator has to decode. The
            # config schema refuses this at boot too — this is the floor
            # under a target that reaches the adapter from anywhere else.
            log.error(
                "refusing to send to conversation_id=%r — discord needs "
                "\"<guild_id>/<channel_id>\" and this names no channel",
                target.conversation_id,
            )
            return SendResult(ok=False)

        # Two vocabularies, two passes, in a fixed order.
        #
        # `mentions` first: these ranges came out of `OutboundComposer`, which
        # already resolved a handle against `identity.toml` AND this room's
        # live roster, so there is nothing left to guess — just a splice at
        # the exact span it measured.
        resolved_text = text
        allowed_users: list[str] = []
        if mentions:
            resolved_text, composed_users = splice_composed_mentions(resolved_text, mentions)
            allowed_users.extend(composed_users)
        # The SECOND pass runs `resolve_outbound_mentions` only over whatever
        # `@` the composer did NOT already resolve — but a splice just wrote
        # `<@100000000000000002>` into `resolved_text`, and that chip has an
        # `@` in it too. A plain `"@" in resolved_text"` check would
        # therefore fire a member-list fetch on EVERY composed
        # mention even when nothing is left to resolve, and worse: an all-
        # digit guild nickname could `str.replace` a match INSIDE a chip
        # already-spliced id, corrupting it. `_SPLICED_MENTION_RE.split`
        # below keeps every `<@id>` chip out of both the "is there more to
        # resolve" check and the replacement itself — only the text BETWEEN
        # chips (the odd-indexed-out, even-indexed `split` groups) is ever
        # examined or rewritten by the fallback.
        segments = _SPLICED_MENTION_RE.split(resolved_text)
        if any("@" in segment for segment in segments[0::2]):
            named_members = await fetch_named_members(
                self._token, base_url=self._base_url, guild_id=guild_id,
                client_timeout=self._timeout,
            )
            fallback_users: list[str] = []
            for i in range(0, len(segments), 2):
                segments[i], ids = resolve_outbound_mentions(segments[i], named_members)
                fallback_users.extend(ids)
            resolved_text = "".join(segments)
            allowed_users.extend(fallback_users)
        # De-duplicated, order preserved: the composer emits one `Mention` per
        # OCCURRENCE of a handle, so the same recipient can legitimately
        # appear twice ("@Tyler Cortez … @Tyler Cortez") and must not be listed
        # twice in `allowed_mentions.users`.
        allowed_users = list(dict.fromkeys(allowed_users))

        headers = {"Authorization": f"Bot {self._token}"}
        # `allowed_mentions` is set on EVERY send, resolved or not: without it
        # Discord parses @everyone/@here/roles out of raw `content` and pings
        # them for real. Restricting to exactly the ids this module spliced
        # is what makes a literal "@everyone" in a reply inert instead of a
        # mass ping nobody asked for.
        message: dict[str, object] = {
            "content": resolved_text,
            "allowed_mentions": {"parse": [], "users": list(allowed_users)},
        }
        if quote is not None and quote.platform_message_id:
            # `message_reference` is Discord's reply. `fail_if_not_exists`
            # FALSE on purpose: the default is true, which turns a reply whose
            # parent was deleted into a REFUSED send — the whole message lost
            # for a decoration. False sends it unthreaded instead, which is
            # this field's own failure posture (`OutboundQuote`: a reply that
            # lost its thread is still a reply).
            #
            # `platform_message_id` is empty for a Signal-shaped quote, and the
            # guard above is what keeps this key off a payload it cannot fill —
            # an absent key, never a present and empty one, the same rule the
            # Signal adapter states for its own decorations.
            message["message_reference"] = {
                "message_id": quote.platform_message_id,
                "fail_if_not_exists": False,
            }
        post_channel = target.thread or channel_id
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                if attachments:
                    # Multipart upload: `payload_json` carries the message
                    # fields; `files[0]` carries the attachment bytes. One
                    # request, Discord's documented shape.
                    form = aiohttp.FormData(quote_fields=False)
                    form.add_field("payload_json", json.dumps(message))
                    for i, a in enumerate(attachments):
                        form.add_field(
                            f"files[{i}]",
                            a.content,
                            filename=a.filename,
                            content_type=a.media_type,
                        )
                    resp = await session.post(
                        f"{self._base_url}/channels/{post_channel}/messages",
                        headers=headers,
                        data=form,
                    )
                else:
                    resp = await session.post(
                        f"{self._base_url}/channels/{post_channel}/messages",
                        headers=headers,
                        json=message,
                    )
                async with resp:
                    if resp.status == 429:
                        body = await resp.json()
                        retry_after = body.get("retry_after") if isinstance(body, dict) else None
                        log.error(
                            "send rate-limited: HTTP 429 (channel=%s, id=%s)",
                            post_channel, request_id,
                        )
                        return SendResult(
                            ok=False,
                            retryable=True,
                            retry_after=float(retry_after) if retry_after is not None else None,
                        )
                    if resp.status not in (200, 201):
                        log.error(
                            "send failed: HTTP %s (channel=%s, id=%s)",
                            resp.status, post_channel, request_id,
                        )
                        return SendResult(ok=False)
                    body = await resp.json()
        except Exception as exc:  # noqa: BLE001 — a send failure is never fatal
            log.error(
                "send failed: %s (channel=%s, id=%s)", type(exc).__name__, post_channel, request_id
            )
            return SendResult(ok=False)
        message_id = body.get("id") if isinstance(body, dict) else None
        log.info("sent %d chars to %s (id=%s)", len(text), target.conversation_id, request_id)
        return SendResult(
            ok=True,
            timestamp=timestamp_ms(int(message_id)) if message_id is not None else None,
        )
