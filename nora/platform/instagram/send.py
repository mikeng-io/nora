"""instagram-bridge send: `POST /send` and `POST /send-media`
(design/instagram-adoption/03-transport-contract.md). Never raises.

Mirrors `nora.platform.whatsapp.send.WhatsAppSender`'s contract — same session
construction, same "log and return" discipline — because the reader who has
read one should not have to re-read the other. Three things differ, and each
one is a real difference on the wire rather than a stylistic choice.

## 1. Text and media are ALWAYS two messages, and this INVERTS WhatsApp

`slidetypes.SendMediaRequest` has fields for attachment, thread, threading id
and reply — and **no text field of any kind** (`03` §"Outbound"). A caption
cannot be expressed on this wire; `POST /send-media` carries no caption field
and ignores one if sent.

WhatsApp's sender puts the reply text in `caption` on the first attachment and
DELIBERATELY SKIPS `/send`, so the group does not hear the reply twice.
Copying that shape here silently drops the text — the message goes out as a
bare image and the words are gone, with a 200 on every call. So on Instagram:
`POST /send` with the text first, then one `POST /send-media` per attachment.

The text goes FIRST on purpose. If the media upload fails, the group has
already heard what Nora said; the reverse order would leave an image with no
explanation of why it arrived.

## 2. 409 is a checkpoint and is NOT retryable

`03`'s status table: 409 means the Instagram session is checkpointed or logged
out, and clearing it needs a human. Retrying a 409 is how a captcha becomes a
ban, so it is `ok=False` with `retryable=False` and an error line that says a
human is needed. 503 (socket down or reconnecting) stays retryable, the same
as WhatsApp's — the bridge heals that one on its own.

500 is the fail-closed default the bridge chose deliberately: 503 is a CLAIM
that retrying will work, and an unclassifiable refusal has not earned it.

## 3. `mentions` are FORWARDED, not ignored — and the TEXT is rewritten to match

WhatsApp v1 accepts and ignores them. Instagram's `/send` takes
`mentions: [{fbid, offset, length}]` and `02` measured the round trip, so the
field is filled — a decoration parameter that the wire supports and the
adapter dropped would be a capability lost at the last inch.

**Forwarding the fbid was not enough — measured live, 2026-09-22.** Nora sent
`"@Miles Novak 同一條友嚟㗎，唔使歡迎兩次"` with a correctly-resolved `mentions`
entry (fbid right, offsets right) and it rendered as PLAIN TEXT, no ping.
Confirmed against upstream mautrix-meta (`igconv/from-matrix.go` +
`textfmt/from-matrix.go`): the reference client builds the request text as
`"@" + mention.Name` — the account's OWN username — and sizes `offset`/
`length` off THAT string, never off whatever the sender typed. Instagram
renders a mention chip only when the span at `offset:offset+length` reads,
literally, `"@" + username`; a display name in that span (all `identity.toml`
gives a model to write with — `nora.agent.outbound`'s whole `_handle_index`
vocabulary is names, first names and aliases, never a handle) is accepted and
silently not a mention.

So `_apply_mention_handles` below SPLICES the text before it leaves: every
`Mention` carrying a `.handle` (`nora.agent.outbound._mentions` populates it
from `Persons.instagram_handle`, an operator-authored `username` beside the
matching `fbid` in `identity.toml`) has its `start:length` span rewritten to
`"@" + handle`, left to right, carrying forward how much every earlier
rewrite grew or shrank the string — see that function's own docstring for why
this is the one place in the codebase that needs the OPPOSITE direction from
`nora.platform.discord.mention_resolve.splice_composed_mentions`: Discord's
splice never has to report an offset back, and this one does, for every
mention in the message, substituted or not. A `Mention` with no `.handle` (no
`username` on file yet) is forwarded with its span unchanged, only its
`offset` corrected for any earlier rewrite in the same message — today's
degraded-but-not-wrong behaviour: the fbid still rides in `mentions`, the text
just stays whatever Nora wrote and no chip renders.

**The offset unit is a narrower ambiguity than it was.** `Mention.start`/
`length` are UTF-16 code units (signal-cli's documented unit, and the one
`nora.domain.utf16` computes); `03` pins the field NAMES and never states
Instagram's own unit. `utf16_index` is what turns a `Mention`'s UTF-16 offset
into the Python string index this module slices the ORIGINAL text with — the
same conversion Discord's splice needs and for the same reason (`nora.domain.
utf16`'s own docstring) — so a rewritten mention's span is correct in Python
terms whatever the platform's wire unit turns out to be; what remains
unmeasured is only whether Instagram's OWN `offset`/`length` on the WIRE want
UTF-16 units or something else, and that is still true of every Mention this
module has ever forwarded, substituted or not. No probe has isolated it, and
none was run for this fix (composing a live Instagram mention to find out is
exactly the write this repo's Hard Limits forbid taking against a real
account) — this is the line to measure first if a chip ever renders over the
wrong span.

`styles` and `preview` stay accepted-and-IGNORED: the port's own docstring
says a platform with no such concept ignores them, and this wire has neither.

1:1 sends are REFUSED — `conversation_type != "group"` logs an error and
returns `SendResult(ok=False)` without an HTTP call. Deny-by-default this
phase, exactly like Signal v2, Discord v1 and WhatsApp v1. Note the limit of
what this can catch: the inbound event carries no group/DM discriminator
(`parser` docstring), so an Instagram DM arrives typed `group` and this guard
never sees it. It still fires for every caller that builds a
`ConversationTarget` itself.
"""  # noqa: RUF002 — the quoted live message keeps its fullwidth Cantonese comma

from __future__ import annotations

import base64
import logging

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
from ...domain.utf16 import utf16_index, utf16_len

log = logging.getLogger("nora.instagram.send")


def _retry_after_seconds(value: str | None) -> float | None:
    """Parse a `Retry-After` header as seconds — delta-seconds form only,
    mirroring `nora.platform.whatsapp.send._retry_after_seconds`. A missing or
    unparseable header degrades to None: the caller still learns the send is
    retryable, just without a hint of how long to wait."""
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _sent_timestamp(body: object) -> int | None:
    """`timestamp_ms` out of the bridge's `{ok, id, timestamp_ms}` result.

    The PLATFORM's own sent-timestamp — what a later reply's quote resolves
    against, the same lesson `nora.platform.signal.send._sent_timestamp`
    records. Anything unparseable degrades to None: a send that reached the
    group must never be reported as failed over a missing field."""
    if not isinstance(body, dict):
        return None
    value = body.get("timestamp_ms")
    # `bool` is an `int` in Python and `True` would silently become 1.
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _apply_mention_handles(
    text: str, mentions: tuple[Mention, ...],
) -> tuple[str, list[dict[str, object]]]:
    """Rewrite `text` for every `Mention` that carries a `.handle`, and return
    the (possibly rewritten) text alongside the wire's `{fbid, offset,
    length}` list — see the module docstring for WHY Instagram needs the text
    itself rewritten and not merely the metadata forwarded.

    **LEFT to right, sorted by `start` ascending — the OPPOSITE of `nora.
    platform.discord.mention_resolve.splice_composed_mentions`, deliberately.**
    Discord's splice never has to report an offset back (`<@id>` carries its
    own identity inline), so splicing right-to-left and leaving every
    UNTOUCHED span's `Mention.start` alone is sufficient there. This function
    also has to hand the BRIDGE a `{offset, length}` for the string it is
    about to become, and a substituted handle is rarely the same UTF-16 length
    as the name Nora drafted (`"@Miles Novak"` is 8 units, `"@nka_bpumvg"` is 11)
    — so the SECOND mention in one message needs an offset shifted by however
    much the FIRST one's substitution grew or shrank the string, and going
    right-to-left would report that first (leftmost) mention's own offset
    correctly while leaving every mention AFTER it wrong by exactly that
    delta. Going left to right and carrying `delta` — the running UTF-16
    length change every earlier substitution introduced — forward is what
    keeps `offset` correct for every mention in the message, substituted or
    not (an unsubstituted mention's own SPAN does not move, but its POSITION
    still shifts if an earlier one in the same message grew or shrank).

    A `Mention` with no `recipient` is dropped rather than sent with an empty
    fbid, unchanged from before this function existed: a mention that names
    nobody is not a mention, and the bridge would have to guess what it meant.
    """
    if not mentions:
        return text, []
    ordered = sorted(range(len(mentions)), key=lambda i: mentions[i].start)
    payload_by_index: dict[int, dict[str, object]] = {}
    delta = 0
    for i in ordered:
        m = mentions[i]
        recipient = (m.recipient or "").strip()
        if not recipient:
            continue
        offset = m.start + delta          # shifted by every EARLIER rewrite
        if m.handle:
            wire_text = f"@{m.handle}"
            start = utf16_index(text, offset)
            end = utf16_index(text, offset + m.length)
            text = text[:start] + wire_text + text[end:]
            new_length = utf16_len(wire_text)
            payload_by_index[i] = {"fbid": recipient, "offset": offset, "length": new_length}
            delta += new_length - m.length
        else:
            payload_by_index[i] = {"fbid": recipient, "offset": offset, "length": m.length}
    # Restore arrival order — `ordered` walked left to right for the splice,
    # which is a REQUIREMENT (the delta above depends on it), but the wire
    # list itself has no ordering contract of its own, and returning it in
    # the order `mentions` arrived in is one less thing to explain later.
    return text, [payload_by_index[i] for i in range(len(mentions)) if i in payload_by_index]


class InstagramSender:
    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def _post(self, path: str, payload: dict, *, conversation_id: str) -> SendResult:
        """One POST against the bridge, with the contract's status mapping."""
        try:
            async with (
                aiohttp.ClientSession(timeout=self._timeout) as session,
                session.post(f"{self._base_url}{path}", json=payload) as resp,
            ):
                if resp.status == 200:
                    return SendResult(ok=True, timestamp=_sent_timestamp(await resp.json()))
                if resp.status == 409:
                    # A CHECKPOINT. Explicitly NOT retryable: the session is
                    # challenged and only a human can clear it, and hammering
                    # a challenged account is how a captcha becomes a ban
                    # (`03`, and the bridge's own status-mapping comment).
                    log.error(
                        "send refused: instagram session is checkpointed or logged out "
                        "(conversation=%s) — a human has to clear it; retrying would not",
                        conversation_id,
                    )
                    return SendResult(ok=False)
                if resp.status == 429:
                    retry_after = _retry_after_seconds(resp.headers.get("Retry-After"))
                    log.error("send rate-limited: HTTP 429 (conversation=%s)", conversation_id)
                    return SendResult(ok=False, retryable=True, retry_after=retry_after)
                if resp.status == 503:
                    # The Instagram socket is down or reconnecting. Transient
                    # by definition — the bridge reconnects on its own, so the
                    # caller may back off and retry rather than latch.
                    log.error("send failed: bridge not connected (conversation=%s)",
                              conversation_id)
                    return SendResult(ok=False, retryable=True)
                log.error("send failed: HTTP %s (conversation=%s)",
                          resp.status, conversation_id)
                return SendResult(ok=False)
        except Exception as exc:               # noqa: BLE001 — a send failure is never fatal
            log.error("send failed: %s (conversation=%s)", type(exc).__name__, conversation_id)
            return SendResult(ok=False)

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
            log.error("refusing to send to conversation_type=%r (1:1 is deny-by-default "
                      "this phase)", target.conversation_type)
            return SendResult(ok=False)

        # `reply_to` is the parent's PLATFORM message id (`mid.$…`), which is
        # the only handle this wire accepts. `OutboundQuote.ts`/`author_id` are
        # Signal's scheme and cannot reconstruct one, so a quote without the
        # id sends unthreaded — a reply that lost its thread is still a reply.
        reply_to = quote.platform_message_id if quote is not None else None

        # Rewritten HERE, not upstream: `nora.agent.outbound.OutboundComposer`
        # owns the drafted TEXT (audited, guarded, identical for every
        # platform) and must keep owning it — see `Mention.handle`'s own
        # docstring for why the wire-only substitution belongs on this side of
        # that boundary. The row `nora.agent.guards.Deliverer` already wrote
        # to A9 before this call still holds the drafted `"@Miles Novak"` form,
        # which is deliberate: it is what Nora MEANT, in the name she and the
        # transcript already use for this person, and it is what Honcho's
        # memory should read back — not a bare `nka_bpumvg` handle a future
        # rename could orphan. The exact precedent already on file for
        # "the audited string and the wire string may differ" is `send_gif`'s
        # `record_text` (`tests/architecture/test_outbound_offsets_are_
        # computed_last.py`); this is the same shape one layer further out.
        text, mention_payload = _apply_mention_handles(text, mentions)

        result = SendResult(ok=True)
        # `or not attachments` so a text-less, attachment-less send still makes
        # the call and the bridge decides. Returning `ok=True` for a send that
        # never left the process would be a delivery status Nora invented.
        if text or not attachments:
            payload: dict[str, object] = {"room_id": target.conversation_id, "text": text}
            if mention_payload:
                payload["mentions"] = mention_payload
            if reply_to:
                payload["reply_to"] = reply_to
            result = await self._post(
                "/send", payload, conversation_id=target.conversation_id,
            )
            if not result.ok:
                # The words did not land. Sending the attachments anyway would
                # put an image in the room with nothing explaining it, and
                # would report the TEXT's failure as a success.
                return result
            log.info("sent %d chars to %s (id=%s)",
                     len(text), target.conversation_id, request_id)

        for a in attachments:
            media_result = await self._post(
                "/send-media",
                {
                    "room_id": target.conversation_id,
                    "data_base64": base64.b64encode(a.content).decode("ascii"),
                    "filename": a.filename,
                    "mimetype": a.media_type,
                    # No `caption` key, ever: the wire has nowhere to put one
                    # and the bridge ignores it. See the module docstring.
                    **({"reply_to": reply_to} if reply_to else {}),
                },
                conversation_id=target.conversation_id,
            )
            if not media_result.ok:
                return media_result
            result = media_result
        if attachments:
            log.info("sent %d attachment(s) to %s (id=%s)",
                     len(attachments), target.conversation_id, request_id)
        return result
