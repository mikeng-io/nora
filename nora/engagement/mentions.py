"""Rehydrate Signal's @-mention placeholders into names, per tag (DEC-34).

**A Signal mention is not text.** The body carries U+FFFC where the name
appears and the identity lives in a parallel `mentions` array.
`nora.platform.signal.parser._mentioned_ids` reads that array only to decide
whether Nora was tagged; a transcript rendered from the raw body would show
every @-mention as a void. Stored rows look like:

    id 28 | '￼ 邊個係阿豹妹？'
    id 24 | '￼'                      <- a message that was ONLY a mention

That includes mentions of HERSELF: 「@nora 你好」 arrives as 「￼ 你好」, so the
one message shape unambiguously addressed to Nora would be the one she could
not see was addressed to her.

WHERE THIS RUNS. On `TagContext`, once, between `context.assemble` and the
prefill — not inside `nora.agent.prefill`, and not at ingest.

* Not at INGEST, because `messages.body` is an append-only audit of what
  arrived (A9). Rewriting it there would store a rendering, and the rendering
  depends on `identity.toml`, which changes when a human joins. `raw_json`
  already carries the full envelope for every row ever audited, so doing it
  here works retroactively on history nobody has to migrate.
* Not in `prefill`, because resolution needs `Persons` and Nora's own id forms,
  and `nora.agent` may not import `nora.platform` (they are independent
  siblings in the layers contract). `nora.engagement` sits above both.

WHY `Persons` AND NOT `Room`. `Room` is the scope for a LOOKUP: answering "who is in
this group" from her whole address book would leak, so `RoomView` answers it
from the platform's own membership list. A mention is not a lookup: the account
was named INSIDE this conversation, by a member of it, in a message Nora received. Resolving it
discloses nothing the room did not already assert, so intersecting with a
roster would buy no scope and would cost a daemon round trip at the front of
every model call — the one thing `Room`'s laziness is built to avoid.

WHAT AN UNRESOLVED MENTION RENDERS. `@someone`, and a counter — never the
number and never the ACI. `mentions[].name` is on the wire and is NOT used:
on every real stored row it IS the phone number (see `MentionSpan`). The
resolved name is put through `scan_identifiers` as well, so an `identity.toml`
whose `name` field is itself an identifier cannot launder one into the prompt.

ESCAPING (R-13/R-13b). The substitution happens UPSTREAM of the single
`nora.domain.prompt.escape` call every prompt block already makes on the body,
so the inserted name is escaped exactly once, by the same code that escapes the
group-authored text around it. Escaping here as well would emit `&amp;amp;` in
a prompt; escaping in neither place is the R-13 hole. Pinned by
`tests/integration/test_mentions.py::
 test_a_mention_of_a_person_whose_name_carries_markup_is_escaped`.
"""  # noqa: RUF002 - the two rows above are quoted verbatim from data/messages.db

from __future__ import annotations

import dataclasses
import json
import logging
from typing import Any

from nora.agent.egress import scan_identifiers
from nora.domain.models import TagContext, TranscriptRow
from nora.persons import Persons
from nora.platform.signal.parser import PLACEHOLDER, MentionSpan, mention_spans

log = logging.getLogger("nora.engagement.mentions")

# What an @-mention Nora cannot name renders as. Deliberately a WORD and not a
# symbol: `@?` would teach the model that a token can mean "unknown" and invite
# it to emit one, the same argument `_parent_of` makes about `↳ ?`.
UNKNOWN = "@someone"


class MentionRenderer:
    """Resolves one tag's @-mentions to names. Constructed fresh per tag.

    Takes `self_ids` — `PlatformIdentity.aliases`, the same flattened set R-12
    tests membership in — because Nora is deliberately absent from
    `identity.toml` and so resolves to nobody through `Persons`. Without it her
    own mention would render `@someone`, which is worse than the placeholder it
    replaced: it would assert that somebody unknown was tagged.
    """

    def __init__(
        self, *, persons: Persons, display_name: str,
        self_ids: frozenset[str], metrics: Any,
        content_checks: bool = True,
    ) -> None:
        self._content_checks = content_checks
        self._persons = persons
        self._display_name = display_name
        self._self_ids = frozenset(self_ids)
        self._metrics = metrics

    def rehydrate(self, ctx: TagContext) -> TagContext:
        """`ctx` with every mention placeholder replaced, in the message and in
        every transcript row. Returns a new `TagContext`; nothing is mutated."""
        platform = ctx.message.platform
        message = dataclasses.replace(
            ctx.message,
            body=self._render(ctx.message.body, ctx.message.raw, platform=platform),
        )
        return dataclasses.replace(
            ctx,
            message=message,
            transcript=tuple(self._row(row, platform) for row in ctx.transcript),
            lookup_transcript=tuple(
                self._row(row, platform) for row in ctx.lookup_transcript
            ),
        )

    def _row(self, row: TranscriptRow, platform: str) -> TranscriptRow:
        """An EDITED row carries two texts from two envelopes:
        its body is the latest edit's, whose
        spans index into `edit.raw_json`, and `edit.was` is the first text,
        whose spans index into the row's own `raw_json`. Each is looked up in
        the envelope it came from — the rule `mention_spans` exists to keep."""
        if row.edit is None:
            return dataclasses.replace(
                row, body=self._text(row.body, row.raw_json, row.id, platform),
            )
        return dataclasses.replace(
            row,
            body=self._text(row.body, row.edit.raw_json, row.id, platform),
            edit=dataclasses.replace(
                row.edit,
                was=self._text(row.edit.was, row.raw_json, row.id, platform),
            ),
        )

    def _text(
        self, text: str | None, raw_json: str | None, row_id: int, platform: str,
    ) -> str | None:
        if not text or PLACEHOLDER not in text or not raw_json:
            # The cheap reject, and it covers every one of Nora's own rows:
            # `Deliverer` stores a `raw` of tag bookkeeping, not an envelope.
            return text
        try:
            event = json.loads(raw_json)
        except (TypeError, ValueError):
            log.warning("row %s has unreadable raw_json; leaving its body as stored", row_id)
            return text
        if not isinstance(event, dict):
            return text
        return self._render(text, event, platform=platform)

    def _render(self, body: str, event: dict[str, Any], *, platform: str) -> str:
        if not body or PLACEHOLDER not in body:
            return body
        spans = mention_spans(event)
        if not spans:
            return body
        # RIGHT TO LEFT. `start`/`length` index the body as it was BEFORE any
        # substitution, and a handle is longer than the one character it
        # replaces, so a left-to-right pass shifts every later span by the
        # growth of every earlier one. Descending order makes every edit land
        # strictly to the RIGHT of every span not yet applied, so no offset a
        # later iteration reads has been touched. (Sorting rather than
        # trusting the wire order: the array's order is not specified.)
        out = body
        for span in sorted(spans, key=lambda s: s.start, reverse=True):
            end = span.start + span.length
            if span.start < 0 or end > len(out) or span.length <= 0:
                continue
            if set(out[span.start:end]) != {PLACEHOLDER}:
                # The offsets do not land on a placeholder, so they are not
                # describing this body. Leave the text exactly as stored rather
                # than cutting a hole in somebody's message on a guess.
                log.warning("mention offsets do not land on a placeholder; leaving the body")
                continue
            out = out[:span.start] + self._handle(span, platform) + out[end:]
        return out

    def _handle(self, span: MentionSpan, platform: str) -> str:
        if self._self_ids & set(span.ids):
            return f"@{self._display_name}"
        for value in span.ids:
            ref = self._persons.resolve(platform, value)
            if ref is None:
                continue
            # `scan_identifiers`, not `scan_reply`: this text is going into the
            # PROMPT, not into the room, so a model name in somebody's name is
            # not a disclosure — only an identifier is (DEC-30).
            if scan_identifiers(ref.name, content_checks=self._content_checks):
                break
            return f"@{ref.name}"
        self._metrics.inc("nora_unmapped_mentions_total", platform=platform)
        return UNKNOWN
