"""The upgrade note: once per version, per room, in her own voice (DEC-97, R-42).

WHAT THIS MODULE IS RESPONSIBLE FOR
-----------------------------------
The DECISION and the LEDGER — is there a version nobody has been told about, and
has it been taken for this room yet. It owns nothing else: the turn belongs to
`nora.app.fire.FirePath`, the words belong to the operator (a tag annotation,
carried in the baked manifest), and the voice belongs to Nora.

That split is not tidiness. R-1b
(`tests/architecture/test_only_two_paths_can_send.py`) pins `deliver.build` to
exactly two callers, so a module that wanted to post its own announcement could
not — every guard, the audit-first write, the Honcho feed and the Langfuse trace
hang off that one construction site, and a second outbound path would have to
re-prove all of them. This module therefore hands `FirePath` an `Event` and
reads back its outcome string.

WHY IT FIRES AT MOST ONCE, AND WHY THAT IS THE STRONGER GUARANTEE
-----------------------------------------------------------------
The claim is an INSERT on a primary key (`SqlAnnouncementLog.claim`), taken
BEFORE the turn runs. A row existing — in any state — bars a second
announcement, so a crash mid-turn, a restart, and a container crash-looping every
four seconds all resolve to silence rather than to a second note. This is
R-37b's at-most-once decision applied to a second kind of unprompted speech, and
for the same reason: nothing links a claim to a message that may already have
reached the group, so a retry double-posts anything that died after egress. An
upgrade note nobody hears costs a `git tag` and a redeploy; one posted twice
cannot be taken back.

WHY AN EMPTY NOTE IS SILENCE, NOT A GENERATED ONE
--------------------------------------------------
The announcement relays what the operator WROTE about a version. When a version
carries no annotation the manifest's `notes` is empty, and this module says
nothing at all rather than falling back to the commit subjects `changes` holds.
Auto-generated subjects are not a note anybody wrote, and a Nora who announces
«refactor(agent): tidy the registry» to a group chat is worse than a Nora who
announces nothing. It is counted, so the silence is visible.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from nora.domain.events import Event
from nora.domain.framing import AnnouncementHeader
from nora.engagement.slots import ConversationSlots, Key
from nora.infra.sql.announcements import DONE, FAILED, SqlAnnouncementLog
from nora.infra.version import OK, VersionManifest
from nora.obs.metrics import Metrics

from .fire import SENT

log = logging.getLogger("nora.app.announce")

# The `nora_version_announcements_total{status}` vocabulary. The four `failed_*`
# / `sent` values are the fire path's own §8.1 outcomes, passed straight through
# so this counter and `nora_reminders_fired_total` describe an unprompted turn's
# fate in the same words. The three below are this module's own decisions.
ALREADY = "already_announced"
NO_NOTE = "no_note"
UNAVAILABLE = "unavailable"
DEFERRED = "deferred"

# Who the release note is attributed to in `<tag_message from="…">`. Not a
# person and deliberately not a person_id: nobody asked for this turn, and
# naming a human as its author would put words in their mouth. Escaped where it
# is used, like every other operator-authored string that reaches a prompt.
SPEAKER = "the people who build you"


@dataclass(frozen=True)
class AnnouncementTarget:
    """One room to announce into.

    All three parts of the conversation key, for `Event`'s reason: a value
    carrying the id alone can build neither a `ConversationTarget` nor the
    `ConversationSlots` key. `conversation_type` defaults to `group` because
    `policy.toml`'s `[[conversations]]` are groups — DMs are a separate section
    the Signal parser still drops entirely.
    """

    platform: str
    conversation_id: str
    conversation_type: str = "group"


class VersionAnnouncer:
    """One boot's sweep: announce the running version wherever it has not been.

    Every collaborator arrives through `__init__`, the clock included — the
    ledger stamps when a version was taken, and a module that read the wall
    clock itself could not be asserted at a fixed instant.
    """

    def __init__(
        self,
        *,
        manifest: VersionManifest,
        store: SqlAnnouncementLog,
        targets: Sequence[AnnouncementTarget],
        speak: Callable[[Event], Awaitable[str]],
        slots: ConversationSlots,
        metrics: Metrics,
        clock: Callable[[], datetime],
    ) -> None:
        self._manifest = manifest
        self._store = store
        self._targets = tuple(targets)
        self._speak = speak
        self._slots = slots
        self._metrics = metrics
        self._clock = clock

    async def run(self) -> None:
        """The whole sweep. Raises nothing: it is spawned at startup, and a
        version note is the least important thing this process does — a fault
        here must not take down a boot that is otherwise healthy.

        Per target rather than per sweep, for `_resolve_all`'s reason
        (`nora.app.heartbeat`): a room whose turn explodes must not cost the
        rooms ordered after it their announcement.
        """
        lookup = self._manifest.current()
        if lookup.status != OK or lookup.entry is None:
            # «I can't check», never «there is nothing» (R-27). An unstamped
            # build announces nothing and says why, rather than announcing a
            # version it guessed.
            log.warning(
                "version announcement skipped: the build stamp is %s (%s)",
                lookup.status, lookup.reason or "no reason recorded",
            )
            self._metrics.inc("nora_version_announcements_total", status=UNAVAILABLE)
            return
        entry = lookup.entry
        if not entry.notes:
            log.info(
                "version %s carries no release note, so there is nothing to "
                "announce — annotate the tag (git tag -m) and rebuild if there "
                "should have been", entry.version,
            )
            self._metrics.inc("nora_version_announcements_total", status=NO_NOTE)
            return
        for target in self._targets:
            try:
                await self._announce(entry.version, entry.notes, target)
            except Exception:
                log.exception(
                    "version announcement failed for %s; the rest of the sweep "
                    "continues", target.conversation_id,
                )
                self._metrics.inc("nora_errors_total", stage="announce")

    async def _announce(
        self, version: str, notes: str, target: AnnouncementTarget,
    ) -> None:
        """One room: slot, claim, turn, ledger — in that order, and the order is
        the design.

        The SLOT is taken first and with `try_acquire`, the heartbeat's rule
        (`nora.app.heartbeat`): a tag owns its conversation, and an announcement
        neither queues behind one nor sheds it. Nothing is claimed in that case,
        so the next boot finds the version unannounced and tries again — which
        is right, because nothing was said.

        The CLAIM comes before the turn, which is what makes at-most-once real:
        a process that dies between here and the group leaves the version taken,
        and the next boot stays quiet about it.
        """
        slot: Key = (target.platform, target.conversation_id)
        if not self._slots.try_acquire(slot):
            log.info(
                "version announcement deferred in %s: the conversation slot is "
                "held", target.conversation_id,
            )
            self._metrics.inc("nora_version_announcements_total", status=DEFERRED)
            return
        try:
            claimed = await self._store.claim(
                version=version, platform=target.platform,
                conversation_id=target.conversation_id, now=self._clock(),
            )
            if not claimed:
                # The ordinary path on every boot after the first. INFO, not a
                # warning: this is the rule working.
                log.info(
                    "version %s was already announced in %s; staying quiet",
                    version, target.conversation_id,
                )
                self._metrics.inc("nora_version_announcements_total", status=ALREADY)
                return
            outcome = await self._speak(_occasion(version, notes, target))
            await self._store.resolve(
                version=version, platform=target.platform,
                conversation_id=target.conversation_id,
                state=DONE if outcome == SENT else FAILED, outcome=outcome,
            )
            log.info(
                "version %s announcement in %s: %s",
                version, target.conversation_id, outcome,
            )
            self._metrics.inc("nora_version_announcements_total", status=outcome)
        finally:
            # ONE call in a `finally`, the tag path's and the tick's rule: a turn
            # that dies must not strand whatever queued behind it.
            self._slots.release_and_dispatch(slot)


def _occasion(
    version: str, notes: str, target: AnnouncementTarget,
) -> Event:
    """The release note, as an occasion to speak.

    `person_id=None`: nobody asked. That is what stops `FirePath` refusing the
    turn under §12's «this requester is no longer in identity.toml» rule, which
    is about a grant this occasion never held.
    """
    return Event(
        platform=target.platform,
        conversation_id=target.conversation_id,
        conversation_type=target.conversation_type,
        person_id=None,
        # The operator's own words, unedited — she is relaying a note, so the
        # note has to arrive intact. `build_tag_message` escapes it into
        # `<tag_message>` (R-13) exactly as it escapes a person's sentence.
        body=notes,
        speaker=SPEAKER,
        header=lambda tagger, now: AnnouncementHeader(version).render(),
        kind="version_announcement",
    )
