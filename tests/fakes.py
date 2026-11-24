"""Offline test doubles shared across the suite. Grows as the plan progresses."""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable
from typing import Any

from nora.domain.managed import (
    ManagedAccount,
    ManagedError,
    ManagedPerson,
    OperationRecord,
    Outcome,
    OutcomeStatus,
    complete,
    denied,
    unavailable,
)
from nora.domain.models import ConversationTarget, NormalizedMessage
from nora.domain.ports import (
    AdapterHealth,
    PlatformIdentity,
    SendResult,
)
from nora.people.app.managed_admission_identity import AdmissionIdentity
from nora.people.app.managed_admission_probe import AdmissionFacts
from nora.testing.managed import FakeBindingStore


class SinkSender:
    """A PlatformAdapter that records sends instead of performing them (spec E.3)."""

    platform = "signal"

    def __init__(
        self, *, succeed: bool = True, account: str = "+00000000000",
        timestamp: int | None = None,
    ) -> None:
        self.sent: list[tuple[ConversationTarget, str]] = []
        self.sent_mentions: list[tuple] = []
        self.sent_styles: list[tuple] = []
        self.sent_attachments: list[tuple] = []
        self.events: list[dict] = []
        self._succeed = succeed
        self._account = account
        # The durable owner a `bind_owner` copy carries — `Deliverer` reads
        # `action_owner` on the adapter, exactly like `BridgeAdapter`.
        self._action_owner: Any = None
        # One entry per `send`: the owner that send was signed under (None
        # on the unbound adapter itself).
        self.sent_owners: list[Any] = []
        # The platform's OWN sent-timestamp, which real signal-cli returns and
        # a reply's `quote.id` points at. `None` — the default
        # — is the shape of a daemon that answered without one.
        self._timestamp = timestamp

    async def listen(self, on_event: Callable[[dict], Awaitable[None]]) -> None:
        for event in self.events:
            await on_event(event)

    async def parse(self, raw_event: dict) -> NormalizedMessage | None:
        from nora.platform.signal.parser import parse_event

        return parse_event(raw_event, own_account="+00000000000")

    async def resolve_identity(self) -> PlatformIdentity:
        return PlatformIdentity(platform=self.platform, primary=self._account)

    async def send(
        self, target: ConversationTarget, text: str,
        mentions: tuple = (), styles: tuple = (), preview: object | None = None,
        attachments: tuple = (),
        quote: object | None = None,
    ) -> SendResult:
        """Accepts (and records) the port's decoration parameters.

        Not optional politeness: `Deliverer` passes all three on EVERY send, so
        a double that did not accept them would `TypeError` on a plain message —
        which is the point of the port having grown them rather than the
        `Deliverer` passing them conditionally. Recorded rather than dropped so
        an integration test can assert an @-mention or a bold range actually
        reached the transport, not merely that the composer built one.
        """
        self.sent.append((target, text))
        self.sent_mentions.append(tuple(mentions))
        self.sent_styles.append(tuple(styles))
        self.sent_attachments.append(tuple(attachments))
        self.sent_owners.append(self._action_owner)
        return SendResult(ok=self._succeed, timestamp=self._timestamp)

    async def health(self) -> AdapterHealth:
        return AdapterHealth(platform=self.platform, connected=True, last_event_age_seconds=0.0)

    @property
    def action_owner(self) -> Any:
        """The bound durable owner, or None when unbound — `BridgeAdapter`'s
        property mirrored so `Deliverer` treats a bound copy identically."""
        return self._action_owner

    def bind_owner(self, owner: Any) -> SinkSender:
        """A facade carrying the durable owner into its sends. The recorded
        `sent` lists are the endpoint-equivalent: shared with the parent,
        like `BridgeAdapter` shares the bridge endpoint."""
        clone = copy.copy(self)
        clone._action_owner = owner
        return clone

    @property
    def texts(self) -> list[str]:
        return [text for _, text in self.sent]


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FixedModelSource:
    """A `nora.agent.factory.ModelSource` stand-in that hands out ONE given model.

    For the tests that drive the REAL Strands loop against a stub `Model` — the
    turn-ending probe, the stop-reason probe, the scripted-agent fidelity probe
    — where the model is the instrument being read and there is exactly one
    turn, so per-tag freshness is not the property under test.

    **Deliberately the same object every time, and that is not the
    answers-everything-the-same trap**: the property this class does NOT hold
    is the subject of its own probe file, `tests/test_model_per_tag.py`, which
    uses a source that builds a fresh model per `create()` and asserts on what
    each tag's model call actually saw. Reach for this one only when a test
    wants a single model it can inspect afterwards.
    """

    def __init__(self, model: object) -> None:
        self._model = model
        self.creates = 0

    def create(self) -> object:
        self.creates += 1
        return self._model


def dreamer_runtime_for(
    *, models: Any, retry: Any, metrics: Any,
) -> Any:
    """A minimal `DreamerRuntime` for tests that drive a session-shaped
    adaptor's model call through the real `run_agent` route — the boundary
    tests patched out (`factory.create_dream_agent`/`invoke`) or drove with a
    stub model before the sibling `LiveCognitionAgentRunner` was removed
    (node 30)."""
    from nora.agent.dreamer import DreamerRuntime

    class _Tools:
        def read_only(self) -> frozenset[str]:
            return frozenset()

        def tool_param_names(self) -> dict[str, frozenset[str]]:
            return {}

    return DreamerRuntime(
        models=models, tools=_Tools(), retry=retry, metrics=metrics,
    )


class FakeAdmissionProbe:
    """The `AdmissionProbe` seam over a `FakeBindingStore` — exposes the
    rows the port reads hide (holds, journals, revoked accounts, suspended
    people) and models the two durable publication transactions: a failure
    inside `publish_held_person` or `complete_import` leaves nothing
    behind, exactly like the SQL rollback. `fail_hold`/`fail_finish`
    inject those pre-commit failures."""

    def __init__(self, store: FakeBindingStore) -> None:
        self._store = store
        self.fail_hold = False
        self.fail_finish = False

    async def facts(self) -> AdmissionFacts:
        store = self._store
        return AdmissionFacts(
            persons=frozenset(
                (p.person_id, p.lifecycle.value, p.kratos_identity_id is not None)
                for p in store.people.values()
            ),
            accounts=frozenset(
                (a.account_id, a.person_id, a.state.value)
                for a in store.accounts.values()
            ),
            identifiers=frozenset(
                (i.platform, i.kind, i.value) for i in store.owners
            ),
            aliases=frozenset(store.aliases.items()),
            memberships=frozenset(
                (pid, scope.kind.value, scope.id, m.state.value)
                for (pid, scope), m in store.memberships.items()
            ),
            supports=frozenset(
                (pid, scope.kind.value, scope.id, source)
                for (pid, scope), sources in store.supports.items()
                for source in sources
            ),
            operations=frozenset(
                (r.operation_id, r.kind, r.state.value)
                for r in store.operations.values()
            ),
            holds=frozenset(store.identity_holds),
            blocks=len(store.blocks),
            link_rows=0,
            scoped_names=len(store.scoped_names),
        )

    async def publish_held_person(
        self, *, person: ManagedPerson, accounts: tuple[ManagedAccount, ...],
        operation_id: str,
    ) -> Outcome:
        store = self._store
        store._up()
        if not operation_id.strip():
            raise ManagedError("an identity hold requires an owning operation")
        # One durable commit, modelled: the store writes land on snapshots
        # and any failure — a foreign claim or the injected hold outage —
        # restores them, so no part of the person survives a failed commit.
        saved = (dict(store.people), dict(store.accounts), dict(store.owners))
        try:
            if not accounts:
                store.people.setdefault(person.person_id, person)
            for account in accounts:
                outcome = await store.register(person=person, account=account)
                if outcome.status is not OutcomeStatus.COMPLETE:
                    raise ManagedError(outcome.detail)
            if self.fail_hold:
                raise RuntimeError("injected hold failure")
        except Exception:
            store.people, store.accounts, store.owners = saved
            raise
        store.identity_holds.add((person.person_id, operation_id))
        return complete()

    async def complete_import(
        self, record: OperationRecord, *, person_ids: tuple[str, ...]
    ) -> Outcome:
        store = self._store
        store._up()
        stored = store.operations.get(record.operation_id)
        if stored is None:
            return denied("absent-operation")
        if stored.kind != record.kind or stored.snapshot != record.snapshot:
            return denied("effect-conflict")
        if self.fail_finish:
            raise RuntimeError("injected finish failure")
        store.operations[record.operation_id] = record
        store.identity_holds -= {
            (pid, record.operation_id) for pid in person_ids
        }
        return complete()


class FakeAdmissionKratos:
    """The `AdmissionKratos` seam over the same record shape
    `FakeIdentityRegistry` keeps — one identity per `external_id`, the
    create carrying its owner marker into `metadata_admin`, `down` the
    outage. Pass a `FakeIdentityRegistry.identities` dict to share one
    Kratos world; the records stay readable by both fakes."""

    def __init__(
        self, identities: dict[str, dict[str, Any]] | None = None
    ) -> None:
        self.identities = identities if identities is not None else {}
        self.down = False

    @staticmethod
    def _view(external_id: str, record: dict[str, Any]) -> AdmissionIdentity:
        return AdmissionIdentity(
            identity_id=record["id"],
            external_id=external_id,
            state=record["state"],
            traits=dict(record["traits"]),
            metadata_admin=copy.deepcopy(record.get("metadata_admin")),
        )

    async def create(
        self, *, external_id: str, traits: dict[str, Any], owner: dict[str, Any]
    ) -> tuple[Outcome, AdmissionIdentity | None]:
        if self.down:
            return unavailable("down"), None
        if external_id in self.identities:
            return denied("external-id-conflict"), None
        self.identities[external_id] = {
            "id": f"kratos-admission-{len(self.identities) + 1}",
            "state": "active",
            "traits": dict(traits),
            "metadata_admin": copy.deepcopy(owner),
        }
        return complete(), self._view(external_id, self.identities[external_id])

    async def read(
        self, *, external_id: str
    ) -> tuple[Outcome, AdmissionIdentity | None]:
        if self.down:
            return unavailable("down"), None
        record = self.identities.get(external_id)
        if record is None:
            return denied("absent"), None
        return complete(), self._view(external_id, record)
