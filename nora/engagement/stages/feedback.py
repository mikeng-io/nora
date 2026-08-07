"""Stage: feedback — the tag path's one write to the semantic store (spec C.7).

**One peer's stream holds one speaker's words. Nothing else.** (DEC-36.)

This used to feed a rendered two-speaker transcript —
`f"[{tagger}]: {body}\\n[{nora}]: {reply}"` — under `ctx.tagger.honcho_peer_id`.
Honcho's deriver is handed that stream as the "Target peer"'s own messages and
draws conclusions ABOUT that peer from all of it, so every reply Nora ever sent
became evidence about the person she was answering. That is the one Honcho
failure in this repo that a backfill cannot undo: the store keeps the
CONCLUSION, not the text it came from, so re-feeding a clean corpus adds good
conclusions beside the bad ones rather than replacing them. Fixed first for
that reason — everything else about this subsystem is downstream of what is in
the corpus.

**Nora's reply is not fed at all, and that is a decision rather than an
omission.** Three arguments, in the order they were made:

* Under the tagger's peer it is the bug above.
* Under a `nora` peer it needs a session that is a CONVERSATION. Honcho
  sessions do hold multiple peers (verified against the live instance), but
  `HonchoClient.ensure_session` builds one session PER PERSON (`nora-<peer>`),
  so today "Nora's reply" would land inside a session that means *Miles's
  private stream* and produce a `nora` peer whose entire corpus is her own
  output. Session-per-conversation is deferred work, and this stage is not the
  place to invent half of it.
* ADR 0001's operative test — *could she have read this in the buffer?* — says
  yes. Her reply is in the Postgres record store, which is the RECORD, and `chat_earlier` already
  reaches it. Feeding it to the INTERPRETATION store costs deriver time to
  learn something she can already look up.

`tagger_name`, `display_name` and `deliverer` are gone from the signature, not
merely unused: with no handle on the reply this stage is structurally unable to
feed it, which is a stronger guarantee than a comment saying it must not.

**Known and deliberately not fixed here:** `ctx.message.body` was ALREADY fed
under this same peer, into this same session, by `nora.ingest.stages.
memory_feed` — that stage runs for every message before the engage gate, so it
runs for the tagging message too. This write is therefore a verbatim duplicate
and costs a second derivation pass over the same text. Removing it is a
separate decision (it deletes this stage, and the session-per-conversation work
is likely to want a tag-path write back) and it is not corrupting, so it is
recorded rather than folded in. See DEC-36 and ADR 0001's follow-on list.

The result is COUNTED, on the same two series the ambient path uses
(`nora.ingest.stages.memory_feed`). It used to be discarded, which made this
the last Honcho write whose failure left no trace anywhere — and the reason
that matters is not hypothetical: `HonchoClient.ensure_session` once POSTed to
a list endpoint, so `feed` returned `False` without making an HTTP call and
Nora's semantic memory received nothing for the deployment's entire life. If
that class of drift recurs, the ambient counters go red while the tagging path
stays invisible, and an operator reading a dashboard would conclude the
memory of actual conversations was healthy when none of it was landing.

`ctx.tagger is None` (an unmapped sender) is checked here as well as by the
caller, defensively, so a caller that forgets that precondition still gets the
right behaviour (no Honcho call for someone we can't name) rather than an
`AttributeError` on `ctx.tagger.honcho_peer_id`.
"""

from __future__ import annotations

from typing import Any

from nora.domain.models import TagContext
from nora.domain.ports import MemoryStore


async def run(*, memory: MemoryStore, ctx: TagContext, metrics: Any) -> None:
    if ctx.tagger is None:
        return
    ok = await memory.feed(peer_id=ctx.tagger.honcho_peer_id, text=ctx.message.body)
    metrics.inc("honcho_feed_success" if ok else "honcho_feed_failure")
