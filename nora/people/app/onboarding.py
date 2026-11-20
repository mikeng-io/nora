"""`PlatformOnboarding` — the scheduled consumer that turns typed
collection discovery into management operations (`identity.md` «Platform
discovery and membership»).

SHAPE. Each `OnboardingRule` is one operator-approved row — a platform's
collection, the scope its member set feeds, and the provisioning service
identity it acts under. Rules are the ONLY source of onboarding: nothing
here scans a platform for collections, and a scope the service is not
approved for is refused by `RosterProvisioning`'s own admission before it
ever reaches a store.

WHAT RUNS. `run()` is one task: it drains three cheap in-memory
notification sets, syncs every rule that is due, then reconciles the
authority journal — all sequenced, so the cost is bounded by the number
of rules, never by event rate.

- `notify_sender` (every inbound message, both runtimes) queues a FIRST
  ENCOUNTER: an untyped sender in a covered collection is observed under
  the rule's identity — the roster stays the truth for membership; the
  sender note only makes the person resolvable before it lands. Each
  note re-derives the sender's provable identifier group, so a pair the
  provider reveals later still enriches the SAME account.
- `notify_observation` (the bridged/direct membership lane) and
  `notify_resumed` (a gateway resume) mark the matching rule dirty, so an
  add/remove/revise — or a gap in those — reconciles promptly instead of
  waiting out the interval.
- The interval itself is the repair: a complete snapshot establishes
  departures, a partial or unavailable read never can, and the linear
  retry streak keeps a downed platform from pinning the loop. A complete
  snapshot carries departure evidence only while its own upstream-read
  stamp is fresh — a stale, undated or future-stamped roster is
  unavailable, never a departure.

`RosterProvisioning` is the one mutation gate: a sync is journaled under
a deterministic operation id (`onboard:<rule>:<observed_ms>:<digest>`),
so a restarted pass with the same roster replays and a different roster
is a new operation — never a silent rewrite.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from nora.domain.managed import (
    CollectionSnapshot,
    ObservationFreshness,
    Outcome,
    OutcomeStatus,
)
from nora.domain.managed_ports import BindingStore, CollectionDiscovery
from nora.domain.management import (
    AccountObservation,
    Actor,
    Operation,
    Scope,
    Target,
    TargetKind,
)
from nora.domain.observations import Observation
from nora.people.app.managed import RegistrationService
from nora.people.app.managed_authority import AuthorityService
from nora.people.app.roster_provisioning import RosterProvisioning

log = logging.getLogger("nora.people.onboarding")


@dataclass(frozen=True)
class OnboardingRule:
    """One approved onboarding rule — `composition_onboarding` builds these
    from `[identity.onboarding]`; the actor is the provisioning service's
    own identity, so a rule inherits exactly its approved sources/scopes."""

    name: str
    actor: Actor
    platform: str
    collection: str
    scope: Scope
    discovery: CollectionDiscovery


class PlatformOnboarding:
    """Sequenced initial sync + event-driven + periodic reconciliation."""

    def __init__(
        self,
        *,
        rules: tuple[OnboardingRule, ...],
        provisioning: RosterProvisioning,
        registration: RegistrationService,
        bindings: BindingStore,
        authority: AuthorityService,
        interval_seconds: float,
        retry_seconds: float,
        freshness: ObservationFreshness | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._rules = {rule.name: rule for rule in rules}
        self._provisioning = provisioning
        self._registration = registration
        self._bindings = bindings
        self._authority = authority
        self._interval = interval_seconds
        self._retry = retry_seconds
        self._clock = clock
        self._wall_clock = wall_clock
        self._freshness = freshness or ObservationFreshness(
            max_age_ms=int(interval_seconds * 1000), wall_clock=wall_clock)
        self._wake = asyncio.Event()
        # (rule_name, sender value) — a dedup queue, never a cache: notes
        # coalesce inside one drain, then the set empties; the journaled
        # operation id replays an identical repeat.
        self._senders: set[tuple[str, str]] = set()
        self._dirty: set[str] = set()
        self._next_at: dict[str, float] = {}
        self._streak: dict[str, int] = {}

    # -- notifications (cheap, sync, any lane) --------------------------------

    def notify_observation(self, observation: Observation) -> None:
        """A membership delta marks its collection's rules dirty. The obs'
        `object_ref` is typed by the platform's own discovery — a
        `channel:` ref maps to no collection, so a room object can never
        dirty a guild roster rule."""
        if observation.object_kind != "membership":
            return
        for rule in self._rules.values():
            if rule.platform != observation.scope.platform:
                continue
            if (rule.discovery.collection_ref(
                    object_ref=observation.object_ref) == rule.collection):
                self._dirty.add(rule.name)
        if self._dirty:
            self._wake.set()

    def notify_sender(
        self, *, platform: str, conversation_id: str, sender_id: str
    ) -> None:
        """A message in a covered collection queues the sender for
        first-encounter registration — `collection_of` maps the room to
        its collection (a Discord room to its guild), so a sender outside
        every approved collection queues nothing."""
        if not sender_id:
            return
        for rule in self._rules.values():
            if rule.platform != platform:
                continue
            if not rule.discovery.capabilities.sender_typing:
                continue
            if (rule.discovery.collection_of(
                    conversation_id=conversation_id) == rule.collection):
                self._senders.add((rule.name, sender_id))
        if self._senders:
            self._wake.set()

    def notify_resumed(self, *, platform: str) -> None:
        """A reconnected platform may have missed deltas while down — every
        rule on it goes dirty rather than trusting the gap is empty."""
        self._dirty.update(
            name for name, rule in self._rules.items()
            if rule.platform == platform
        )
        if self._dirty:
            self._wake.set()

    def wrap_on_message(
        self, *, platform: str,
        on_message: Callable[[Any], Awaitable[None]],
    ) -> Callable[[Any], Awaitable[None]]:
        """`on_message` with the sender note first — the registration is
        queued, never awaited inside the turn."""

        async def wrapped(message: Any) -> None:
            self.notify_sender(
                platform=platform,
                conversation_id=message.conversation_id,
                sender_id=message.sender_id)
            await on_message(message)

        return wrapped

    def listen_extras(
        self, adapter: Any, *,
        on_message: Callable[[Any], Awaitable[None]],
    ) -> dict[str, Any]:
        """The full kwargs `adapter.listen` is called with — a platform
        with no rule gets the bare `on_message` it always got, so a
        `SupervisedAdapter` passes exactly the signature it did before
        onboarding existed."""
        platform = getattr(adapter, "platform", "")
        rules = [r for r in self._rules.values() if r.platform == platform]
        if not rules:
            return {"on_message": on_message}
        extras: dict[str, Any] = {
            "on_message": self.wrap_on_message(
                platform=platform, on_message=on_message)}
        # Observation/resumed kwargs go only to an adapter whose `listen`
        # declares them — a bridge adapter's listen idles with `on_message`
        # alone; deltas reach `notify_observation` through the WorkRunner.
        params = inspect.signature(adapter.listen).parameters
        if (
            any(r.discovery.capabilities.member_events for r in rules)
            and "on_observation" in params
            and "on_resumed" in params
        ):
            async def _observed(observation: Observation) -> None:
                self.notify_observation(observation)

            async def _resumed() -> None:
                self.notify_resumed(platform=platform)

            extras["on_observation"] = _observed
            extras["on_resumed"] = _resumed
        return extras

    # -- the loop -------------------------------------------------------------

    async def run(self) -> None:
        log.info(
            "platform onboarding active for %d collection(s): %s",
            len(self._rules),
            ", ".join(sorted(self._rules)))
        while True:
            await self._drain()
            self._wake.clear()
            delay = self._next_delay()
            if delay:
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=delay)

    def _next_delay(self) -> float:
        if self._senders or self._dirty:
            return 0.0
        now = self._clock()
        return max(
            0.0, min(self._next_at.values(), default=now) - now)

    async def _drain(self) -> None:
        """Senders, then dirty rules, then due rules — all sequenced."""
        senders, self._senders = self._senders, set()
        for rule_name, value in sorted(senders):
            await self._register_sender(self._rules[rule_name], value)
        dirty, self._dirty = self._dirty, set()
        for name in sorted(dirty):
            outcome = await self._sync_rule(self._rules[name])
            if outcome is not None and outcome.status is OutcomeStatus.COMPLETE:
                self._streak[name] = 0
                self._next_at[name] = self._clock() + self._interval
            else:
                # A dirty sync that could not read does not wait a whole
                # interval — the retry streak reschedules it like any
                # failed due sync.
                streak = self._streak.get(name, 0) + 1
                self._streak[name] = streak
                self._next_at[name] = self._clock() + self._retry * streak
        now = self._clock()
        for rule in self._rules.values():
            if self._next_at.get(rule.name, 0.0) > now:
                continue
            outcome = await self._sync_rule(rule)
            if outcome is not None and outcome.status is OutcomeStatus.COMPLETE:
                self._streak[rule.name] = 0
                self._next_at[rule.name] = now + self._interval
            else:
                streak = self._streak.get(rule.name, 0) + 1
                self._streak[rule.name] = streak
                self._next_at[rule.name] = now + self._retry * streak
        try:
            await self._authority.reconcile()
        except Exception:
            log.exception("onboarding authority reconcile failed")

    def _observation_fresh(self, snapshot: CollectionSnapshot) -> bool:
        """The shared departure check the mutation boundary re-enforces."""
        return self._freshness.departure(snapshot.observed_ms)

    async def _sync_rule(self, rule: OnboardingRule) -> Outcome | None:
        try:
            snapshot = await rule.discovery.members(
                collection=rule.collection)
            if not snapshot.available:
                log.info(
                    "onboarding %s: roster unavailable (%s)",
                    rule.name, snapshot.detail or "read failed")
                return None
            if (snapshot.complete
                    and not self._observation_fresh(snapshot)):
                log.info(
                    "onboarding %s: complete roster's observation time is "
                    "not fresh (observed_ms=%s) — departure evidence "
                    "unavailable", rule.name, snapshot.observed_ms)
                return None
            return await self._provisioning.apply(
                operation_id=self._operation_id(rule.name, snapshot),
                actor=rule.actor, scope=rule.scope,
                collection=rule.collection,
                members=snapshot.members,
                complete_roster=snapshot.complete,
                observed_ms=snapshot.observed_ms)
        except Exception:
            log.exception("onboarding %s: roster sync failed", rule.name)
            return None

    @staticmethod
    def _operation_id(name: str, snapshot: CollectionSnapshot) -> str:
        """Deterministic per roster content + read instant — a replay, never a silent rewrite."""
        members = sorted(
            (
                [member.classification, sorted(
                    [i.platform, i.kind, i.value]
                    for i in member.identifiers)]
                for member in snapshot.members
            ),
            key=lambda row: (row[1][0] if row[1] else ["", "", ""]),
        )
        digest = hashlib.sha256(json.dumps(
            members, separators=(",", ":")).encode()).hexdigest()[:24]
        return f"onboard:{name}:{snapshot.observed_ms}:{digest}"

    async def _register_sender(
        self, rule: OnboardingRule, value: str
    ) -> None:
        """First-encounter observe under the rule's identity — the roster decides membership."""
        try:
            ids = await rule.discovery.sender_identifiers(
                collection=rule.collection, value=value)
            if (not ids
                    or any(i.platform not in rule.actor.sources for i in ids)):
                # No provable identifiers, or a discovery answer outside the
                # service's approved sources — either way there is nothing
                # this rule may lawfully observe.
                return
            account = min(ids, key=lambda i: (i.platform, i.kind, i.value))
            record = await self._bindings.record_for(account=account)
            digest = hashlib.sha256(json.dumps(
                [[i.platform, i.kind, i.value]
                 for i in sorted(ids, key=lambda i: (i.platform, i.kind, i.value))],
                separators=(",", ":")).encode()).hexdigest()[:24]
            outcome = await self._registration.apply(Operation(
                f"onboard-sender:{rule.name}:{digest}", "observe_account",
                rule.actor,
                Target(TargetKind.ACCOUNT, account=account),
                AccountObservation(frozenset(ids)),
                {"ownership": str(record.revision)
                 if record is not None else "0"}))
            if outcome.status is not OutcomeStatus.COMPLETE:
                # Nothing is remembered — the next note re-observes, and
                # an unfinished journaled operation resumes where it left off.
                log.info(
                    "onboarding %s: sender not registered (%s)",
                    rule.name, outcome.detail)
        except Exception:
            log.exception(
                "onboarding %s: sender observation failed", rule.name)
