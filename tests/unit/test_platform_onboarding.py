"""`PlatformOnboarding` end to end through the real managed services —
discovery → `RosterProvisioning` → bindings/authority over the in-memory
fakes, plus the real Discord/Signal discovery normalizers (`identity.md`
«Platform discovery and membership»):

- an initial complete roster bulk-registers independent people under the
  rule's service identity — all valid identifiers included regardless of account labels; a row
  without identifiers refuses rather than fabricating membership;
- a Signal member's number+uuid pair binds ONE person, never two, and a
  sender typed through the same pair is one observation;
- joins arrive through the membership-observation dirty lane and through
  first-encounter sender typing; a complete follow-up roster is what
  establishes departure — a partial or unavailable read never can;
- departure retracts only the collection's own support: another
  collection's membership and a direct grant survive, and the person is
  never deleted;
- out-of-order and duplicate events are idempotent — a repeated roster
  replays, and operation ids stay deterministic per roster content;
- a gateway resume, not an assumed-quiet gap, dirties the platform's
  rules.
"""

from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

from nora.config_identity import (
    IdentityBackendConfig,
    ManagedOryConfig,
    OnboardingCollectionConfig,
    OnboardingConfig,
    ProvisioningScopeConfig,
    ProvisioningServiceConfig,
)
from nora.domain.identity import Identifier
from nora.domain.managed import (
    CollectionSnapshot,
    ObservedMember,
    ObservedRows,
)
from nora.domain.management import (
    Actor,
    ActorKind,
    Scope,
    ScopeKind,
)
from nora.domain.observations import (
    MembershipPayload,
    Observation,
    Operation,
    SourceScope,
)
from nora.people.app.managed import ManagedServices
from nora.people.app.onboarding import OnboardingRule, PlatformOnboarding
from nora.people.app.roster_provisioning import RosterProvisioning
from nora.platform.discord.discovery import DiscordDiscovery
from nora.platform.signal.discovery import SignalDiscovery
from nora.testing.discovery import ScriptedDiscovery
from nora.testing.managed import (
    FakeAuthorityBackend,
    FakeBindingStore,
    FakeIdentityRegistry,
    FakeLinkRequests,
    FakeTopologySource,
)

UNIT = Scope(ScopeKind.UNIT, "generated-unit")
UNIT_TWO = Scope(ScopeKind.UNIT, "generated-unit-two")
SERVICE = Actor(
    ActorKind.SERVICE, service_id="generated-bridge",
    sources=frozenset({"discord", "signal"}))
GUILD = "100000000000000001"  # Discord collection ids are snowflakes
GROUP = "generated-group"


def guild_member(n: int, **flags) -> dict:
    """A Discord `/guilds/{id}/members` row — `user.id` a snowflake."""
    return {"user": {"id": str(10_000 + n), **flags}}


def signal_member(n: int) -> dict:
    """A `listGroups` member row — the daemon's number+uuid pair."""
    return {"number": f"+852{n:03d}", "uuid": f"00000000-0000-4abc-8def-{n:012d}"}


def guild_scope() -> SourceScope:
    return SourceScope(
        platform="discord", account_key="generated-bot",
        conversation_id=f"{GUILD}/generated-room")


def member_obs(ref: str, operation: Operation = "add") -> Observation:
    return Observation(
        object_kind="membership", scope=guild_scope(), object_ref=ref,
        operation=operation, payload=MembershipPayload(state="member"),
        subject_ref="generated-subject")



def _graph(*, freshness=None) -> tuple[ManagedServices, RosterProvisioning]:
    services = ManagedServices(
        FakeBindingStore(), FakeAuthorityBackend(),
        FakeTopologySource(
            units={UNIT.id: "generated-ws", UNIT_TWO.id: "generated-ws"},
            workspaces={"generated-ws"}),
        FakeIdentityRegistry(), FakeLinkRequests(), freshness=freshness)
    return services, RosterProvisioning(
        bindings=services.bindings, registry=services.registry,
        topology=services.topology, registration=services.registration,
        authority=services.authority)


def _onboarding(
    services: ManagedServices, provisioning: RosterProvisioning,
    *rules: OnboardingRule, interval: float = 300.0,
) -> PlatformOnboarding:
    return PlatformOnboarding(
        rules=tuple(rules), provisioning=provisioning,
        registration=services.registration, bindings=services.bindings,
        authority=services.authority, interval_seconds=interval,
        retry_seconds=60.0)


def _rule(discovery, name: str = "generated-rule", collection: str = GUILD,
          scope: Scope = UNIT, platform: str = "discord") -> OnboardingRule:
    return OnboardingRule(
        name=name, actor=SERVICE, platform=platform,
        collection=collection, scope=scope, discovery=discovery)


_read = 0


def _rows(rows, observed_ms=None):
    """A producer answer stamped with a strictly increasing instant."""
    global _read
    _read += 1
    return ObservedRows(
        rows=tuple(rows),
        observed_ms=int(time.time() * 1000) + _read if observed_ms is None
        else observed_ms)


def _discord(rows):
    """`DiscordDiscovery` over a scripted fetch — the real normalizer."""
    calls = {"n": 0}

    async def fetch(collection: str):
        calls["n"] += 1
        value = rows() if callable(rows) else rows
        return None if value is None else _rows(value)

    return DiscordDiscovery(fetch=fetch), calls


# -- the discovery normalizers themselves ------------------------------------

class TestDiscoveryAdapters:

    async def test_discord_rows_classify_and_paginated_fetch_is_one_read(self):
        rows = [guild_member(1), guild_member(2, bot=True),
                guild_member(3, system=True), {"user": {}}, {"no-user": 1}]
        discovery, _ = _discord(rows)
        snapshot = await discovery.members(collection=GUILD)
        assert snapshot.available and snapshot.complete
        by_class = {}
        for member in snapshot.members:
            by_class.setdefault(member.classification, []).append(member)
        assert [next(iter(m.identifiers)).value
                for m in by_class["person"]] == ["10001"]
        assert {next(iter(m.identifiers)).value
                for m in by_class["bot"]} == {"10002", "10003"}
        assert all(not m.identifiers for m in by_class["unknown"])

    async def test_discord_unavailable_and_empty_never_read_as_empty_roster(self):
        discovery, _ = _discord(None)
        snapshot = await discovery.members(collection=GUILD)
        assert not snapshot.available and not snapshot.members
        discovery, _ = _discord([])
        snapshot = await discovery.members(collection=GUILD)
        # A guild cannot enumerate to zero members — a broken read, not
        # proof everyone left.
        assert not snapshot.available and snapshot.detail == "empty-roster"

    async def test_discord_guild_is_never_a_room_audience(self):
        discovery, _ = _discord([guild_member(1)])
        assert not discovery.capabilities.room_audience
        assert discovery.collection_of(
            conversation_id=f"{GUILD}/generated-room") == GUILD
        # A channel object ref maps to no collection — a channel's member
        # set cannot dirty the guild rule.
        assert discovery.collection_ref(object_ref="channel:123") is None
        assert discovery.collection_ref(object_ref=f"guild:{GUILD}") == GUILD
        assert await discovery.sender_identifiers(
            collection=GUILD, value="not-a-snowflake") is None

    async def test_signal_pairs_number_and_uuid_into_one_member(self):
        rows = [signal_member(1), signal_member(2)]

        async def _fetch(group):
            return _rows(rows)

        discovery = SignalDiscovery(fetch=_fetch, own_account="")
        snapshot = await discovery.members(collection=GROUP)
        assert snapshot.available and len(snapshot.members) == 2
        member = snapshot.members[0]
        assert {i.kind for i in member.identifiers} == {"phone", "uuid"}
        assert {i.platform for i in member.identifiers} == {"signal"}

    async def test_signal_excludes_own_account_and_untypable_row_is_unknown(self):
        rows = [signal_member(1), {"number": "+852999"},
                {"pending": True}]

        async def _fetch(group):
            return _rows(rows)

        discovery = SignalDiscovery(
            fetch=_fetch, own_account="+852999")
        snapshot = await discovery.members(collection=GROUP)
        assert len(snapshot.members) == 2
        assert any(m.classification == "unknown" for m in snapshot.members)
        assert not any(
            i.value == "+852999"
            for m in snapshot.members for i in m.identifiers)

    async def test_signal_sender_types_from_group_pair_before_shape(self):
        rows = [signal_member(1)]

        async def _fetch(group):
            return _rows(rows)

        discovery = SignalDiscovery(fetch=_fetch, own_account="")
        # The uuid-only sealed-sender value resolves through the roster
        # pair — not a guessed bare identifier.
        ids = await discovery.sender_identifiers(
            collection=GROUP, value=signal_member(1)["uuid"])
        assert {i.kind for i in ids} == {"phone", "uuid"}
        bare = await discovery.sender_identifiers(
            collection=GROUP, value="+852123")
        assert {i.kind for i in bare} == {"phone"}
        assert await discovery.sender_identifiers(
            collection=GROUP, value="definitely-not-an-id") is None

    async def test_signal_unavailable_group_is_never_an_empty_roster(self):
        async def _fetch(group):
            return None

        discovery = SignalDiscovery(fetch=_fetch, own_account="")
        assert not (await discovery.members(collection=GROUP)).available

        async def _empty(group):
            return _rows(())

        discovery = SignalDiscovery(fetch=_empty, own_account="")
        assert not (await discovery.members(collection=GROUP)).available


# -- the runtime service ------------------------------------------------------

class TestOnboarding:

    async def test_initial_roster_bulk_registers_independent_people(self):
        services, provisioning = _graph()
        discovery, _ = _discord([guild_member(n) for n in range(120)])
        onboarding = _onboarding(
            services, provisioning, _rule(discovery))
        await onboarding._drain()
        assert len(services.bindings.people) == 120
        assert len({a.person_id for a in services.bindings.accounts.values()}) == 120
        memberships = await services.bindings.members_of(UNIT)
        assert len(memberships) == 120
        assert all(
            m.sources == {f'sync:["generated-bridge","{GUILD}"]'}
            for m in memberships)
        # Second drain: same roster, deterministic replay — no new people.
        await onboarding._drain()
        assert len(services.bindings.people) == 120

    async def test_missing_identifiers_refuse_without_excluding_valid_bot_accounts(self):
        services, provisioning = _graph()
        discovery, _ = _discord(
            [guild_member(1), guild_member(2, bot=True), {"user": {}}])
        onboarding = _onboarding(services, provisioning, _rule(discovery))
        await onboarding._drain()
        # The unknown row made the complete roster unavailable — nothing
        # committed, rather than a half-known member set.
        assert not services.bindings.people

        discovery, _ = _discord(
            [guild_member(1), guild_member(2, bot=True)])
        onboarding = _onboarding(services, provisioning, _rule(discovery))
        await onboarding._drain()
        assert len(services.bindings.people) == 2
        assert (await services.bindings.account_for(
            "discord", "user_id", "10002")) is not None

    async def test_partial_or_unavailable_read_never_departs(self):
        services, provisioning = _graph()
        discovery, _ = _discord([guild_member(1), guild_member(2)])
        onboarding = _onboarding(services, provisioning, _rule(discovery))
        await onboarding._drain()
        assert len(await services.bindings.members_of(UNIT)) == 2

        # An unavailable read cannot remove anyone.
        discovery, _ = _discord(None)
        onboarding = _onboarding(services, provisioning, _rule(discovery))
        await onboarding._drain()
        assert len(await services.bindings.members_of(UNIT)) == 2

        # A partial snapshot merges — the absent member stays. Its stamp
        # must be newer than the drain's accepted mark or it is refused as
        # superseded evidence.
        partial = ScriptedDiscovery(CollectionSnapshot(
            available=True, complete=False,
            members=(ObservedMember(
                frozenset({Identifier("discord", "user_id", "10003")})),),
            observed_ms=int(time.time() * 1000) + 1_000), collection=GUILD)
        onboarding = _onboarding(services, provisioning, _rule(partial))
        await onboarding._drain()
        members = await services.bindings.members_of(UNIT)
        assert len(members) == 3

    async def test_departure_retracts_only_this_collections_support(self):
        services, provisioning = _graph()
        both = [guild_member(1)]
        first, _ = _discord(both)
        second_rows = [guild_member(1)]

        async def second_fetch(collection: str):
            return _rows(list(second_rows))

        second = DiscordDiscovery(fetch=second_fetch)
        onboarding = _onboarding(
            services, provisioning,
            _rule(first, name="rule-a", scope=UNIT),
            _rule(second, name="rule-b", collection="guild-two",
                  scope=UNIT_TWO))
        await onboarding._drain()
        assert len(await services.bindings.members_of(UNIT)) == 1
        assert len(await services.bindings.members_of(UNIT_TWO)) == 1

        # A direct grant on collection A's scope survives A's departure.
        person_id = next(iter(services.bindings.people))
        await services.bindings.set_membership(
            person_id=person_id, scope=UNIT, member=True,
            source="manual-grant")

        # The member leaves collection B only — the guild's new roster is
        # somebody else, never an empty read (empty is `unavailable`).
        second_rows.clear()
        second_rows.append(guild_member(9))
        onboarding.notify_resumed(platform="discord")
        await onboarding._drain()
        a = await services.bindings.memberships_for(person_id)
        by_scope = {m.scope: m for m in a}
        assert by_scope[UNIT_TWO].state.value == "left"
        # The manual grant keeps the person a member of scope A, and the
        # person itself is untouched.
        assert by_scope[UNIT].state.value == "member"
        assert person_id in services.bindings.people

        # Now collection A's roster also drops them: the roster support
        # leaves, the manual grant still stands.
        async def empty_first(collection: str):
            # somebody else only
            return _rows([{"user": {"id": "999999"}}])

        first._fetch = empty_first
        onboarding.notify_resumed(platform="discord")
        await onboarding._drain()
        a = await services.bindings.memberships_for(person_id)
        by_scope = {m.scope: m for m in a}
        assert by_scope[UNIT].state.value == "member"
        assert by_scope[UNIT].sources == {"manual-grant"}

    async def test_membership_observation_dirties_the_matching_rule(self):
        services, provisioning = _graph()
        discovery, calls = _discord([guild_member(1)])
        onboarding = _onboarding(services, provisioning, _rule(discovery))
        await onboarding._drain()
        assert calls["n"] == 1

        # A membership event on ANOTHER guild's set dirties nothing.
        onboarding.notify_observation(member_obs("guild:999"))
        await onboarding._drain()
        assert calls["n"] == 1
        # A channel ref is not the guild's member set — no dirty.
        onboarding.notify_observation(member_obs("channel:123"))
        await onboarding._drain()
        assert calls["n"] == 1
        # The matching guild's add dirties → a re-read picks the join up.
        rows = [guild_member(1), guild_member(2)]
        grown_reads = {"n": 0}

        async def grown(collection: str):
            grown_reads["n"] += 1
            return _rows(rows)

        discovery._fetch = grown
        onboarding.notify_observation(member_obs(f"guild:{GUILD}"))
        await onboarding._drain()
        assert grown_reads["n"] == 1
        assert len(await services.bindings.members_of(UNIT)) == 2

    async def test_sender_first_encounter_registers_without_membership(self):
        services, provisioning = _graph()
        discovery, _ = _discord([guild_member(1)])
        onboarding = _onboarding(services, provisioning, _rule(discovery))
        await onboarding._drain()
        assert len(services.bindings.people) == 1

        # A sender inside the covered conversation space registers under
        # the rule — the roster, not this path, decides membership later.
        onboarding.notify_sender(
            platform="discord",
            conversation_id=f"{GUILD}/generated-room", sender_id="10007")
        await onboarding._drain()
        bound = await services.bindings.account_for(
            "discord", "user_id", "10007")
        assert bound is not None
        memberships = await services.bindings.members_of(UNIT)
        assert len(memberships) == 1  # sender note grants no membership

        # A sender outside every covered collection queues nothing.
        before = len(services.bindings.people)
        onboarding.notify_sender(
            platform="discord", conversation_id="999/other",
            sender_id="10008")
        await onboarding._drain()
        assert len(services.bindings.people) == before

        # Repeating the same sender note cannot re-register.
        onboarding.notify_sender(
            platform="discord",
            conversation_id=f"{GUILD}/generated-room", sender_id="10007")
        await onboarding._drain()
        assert len(services.bindings.people) == before

    async def test_resume_and_out_of_order_events_reconcile_idempotently(self):
        services, provisioning = _graph()
        rows = [guild_member(1)]

        async def fetch(collection: str):
            return _rows(list(rows))

        discovery = DiscordDiscovery(fetch=fetch)
        onboarding = _onboarding(services, provisioning, _rule(discovery))
        await onboarding._drain()
        assert len(await services.bindings.members_of(UNIT)) == 1

        # Events may arrive out of order or twice — the roster read is the
        # truth they converge to, so duplicate notifies cost one re-read.
        onboarding.notify_observation(member_obs(f"guild:{GUILD}", "remove"))
        onboarding.notify_observation(member_obs(f"guild:{GUILD}", "add"))
        onboarding.notify_observation(member_obs(f"guild:{GUILD}", "add"))
        await onboarding._drain()
        assert len(await services.bindings.members_of(UNIT)) == 1

        # The member actually left while the gateway was down; the resume
        # — not the missing event — is what reconciles.
        rows.clear()
        rows.append(guild_member(9))
        onboarding.notify_resumed(platform="discord")
        await onboarding._drain()
        members = await services.bindings.members_of(UNIT)
        assert len(members) == 1
        bound = await services.bindings.account_for(
            "discord", "user_id", "10001")
        assert bound is not None  # the person is never deleted on departure

    async def test_signal_pair_binds_one_person_through_the_runtime(self):
        services, provisioning = _graph()
        rows = [signal_member(1)]

        async def fetch(collection: str):
            return _rows(rows)

        discovery = SignalDiscovery(fetch=fetch, own_account="")
        onboarding = _onboarding(
            services, provisioning,
            _rule(discovery, collection=GROUP, platform="signal"))
        await onboarding._drain()
        assert len(services.bindings.people) == 1
        phone = await services.bindings.account_for(
            "signal", "phone", signal_member(1)["number"])
        aci = await services.bindings.account_for(
            "signal", "uuid", signal_member(1)["uuid"])
        assert phone is not None and phone.person_id == aci.person_id



class TestOnboardingWiring:
    """The composition seam: `listen_extras` gives every adapter exactly
    the kwargs its `listen` declares — nothing more."""

    def test_adapter_with_no_rule_gets_the_bare_on_message(self):
        services, provisioning = _graph()
        discovery, _ = _discord([guild_member(1)])
        onboarding = _onboarding(services, provisioning, _rule(discovery))

        class SignalLikeAdapter:
            platform = "signal"

            async def listen(self, *, on_message):
                ...

        async def on_message(message):
            ...

        extras = onboarding.listen_extras(
            SignalLikeAdapter(), on_message=on_message)
        assert extras == {"on_message": on_message}

    def test_discord_adapter_gets_observation_and_resumed_hooks(self):
        services, provisioning = _graph()
        discovery, _ = _discord([guild_member(1)])
        onboarding = _onboarding(services, provisioning, _rule(discovery))

        class DiscordLikeAdapter:
            platform = "discord"

            async def listen(self, *, on_message, on_observation=None,
                             on_resumed=None):
                ...

        async def on_message(message):
            ...

        extras = onboarding.listen_extras(
            DiscordLikeAdapter(), on_message=on_message)
        assert set(extras) == {"on_message", "on_observation", "on_resumed"}

        class BridgeLikeAdapter:
            platform = "discord"

            async def listen(self, *, on_message):
                ...

        extras = onboarding.listen_extras(
            BridgeLikeAdapter(), on_message=on_message)
        # Bridge deltas arrive through the WorkRunner — its idle listen
        # never receives gateway kwargs.
        assert set(extras) == {"on_message"}


class TestOnboardingConfig:
    """`[identity.onboarding]` — the operator-approved rule shape and its
    cross-checks against the provisioning boundary it acts under."""

    @staticmethod
    def _service(*, sources=frozenset({"discord"}),
                 kinds=("unit",)) -> ProvisioningServiceConfig:
        return ProvisioningServiceConfig(
            token_env="GENERATED_TOKEN",  # noqa: S106 — names an env var, not a secret
            sources=sources,
            scopes=tuple(
                ProvisioningScopeConfig(kind=k, id=f"generated-{k}-scope")
                for k in kinds))

    @staticmethod
    def _rule(**changes) -> dict:
        body = {
            "name": "generated-rule", "service": "generated-svc",
            "platform": "discord", "collection": "100000000000000001",
            "scope": ProvisioningScopeConfig(
                kind="unit", id="generated-unit-scope"),
            "audience": "collection",
        }
        return {**body, **changes}

    def _backend(self, rules, **service_changes) -> IdentityBackendConfig:
        return IdentityBackendConfig(
            backend="managed",
            managed=ManagedOryConfig(
                kratos_admin_url="http://127.0.0.1:4434",
                keto_read_url="http://127.0.0.1:4466",
                keto_write_url="http://127.0.0.1:4467"),
            provisioning={"generated-svc": self._service(**service_changes)},
            onboarding=OnboardingConfig(collections=rules))

    def test_file_mode_and_empty_collections_build_nothing(self):
        # The default deployment: no managed backend, no onboarding.
        assert not IdentityBackendConfig().onboarding.collections
        with pytest.raises(ValidationError):
            IdentityBackendConfig(
                backend="file",
                onboarding={"collections": [self._rule()]})

    def test_every_admission_field_is_checked_against_the_service(self):
        self._backend([self._rule()])  # the admitted shape builds
        with pytest.raises(ValidationError):  # unknown service name
            self._backend([self._rule(service="ghost")])
        with pytest.raises(ValidationError):  # platform outside sources
            self._backend(
                [self._rule(platform="signal")],
                sources=frozenset({"discord"}))
        with pytest.raises(ValidationError):  # scope not approved
            self._backend([self._rule(
                scope=ProvisioningScopeConfig(
                    kind="unit", id="unapproved-scope"))])

    def test_collection_audience_can_never_grant_a_room(self):
        service = self._service(kinds=("room",))
        with pytest.raises(ValidationError):
            IdentityBackendConfig(
                backend="managed",
                managed={
                    "kratos_admin_url": "http://127.0.0.1:4434",
                    "keto_read_url": "http://127.0.0.1:4466",
                    "keto_write_url": "http://127.0.0.1:4467",
                },
                provisioning={"generated-svc": service},
                onboarding={"collections": [self._rule(
                    scope=ProvisioningScopeConfig(
                        kind="room", id="generated-room-scope"))]})
        # And a room audience is only admitted with a room scope.
        with pytest.raises(ValidationError):
            self._backend([self._rule(
                audience="room",
                scope=ProvisioningScopeConfig(
                    kind="unit", id="generated-unit-scope"))])

    def test_unknown_platform_and_duplicate_rules_refuse(self):
        # `telegram` IS a declared platform (OIDC login) — its missing
        # roster capability is refused at composition, not config. What
        # config refuses is a platform with no id-kind vocabulary at all.
        with pytest.raises(ValidationError):
            OnboardingCollectionConfig(**self._rule(platform="myspace"))
        with pytest.raises(ValidationError):
            self._backend([self._rule(name="a"),
                           self._rule(name="a")])
        with pytest.raises(ValidationError):  # same service+platform+collection
            self._backend([self._rule(name="a"),
                           self._rule(name="b")])
        # Distinct platforms are fine even under one service.
        self._backend(
            [self._rule(name="a"),
             self._rule(name="b", platform="signal",
                        collection="generated-group")],
            sources=frozenset({"discord", "signal"}))


class TestComposition:
    """`composition_onboarding.onboarding` — the build the runtime wires:
    managed + declared collections builds rules under the service's own
    identity; anything else builds nothing or refuses at boot."""

    @staticmethod
    def _cfg(*, backend="managed", collections=(),
             platforms=None, sources=None, scopes=None):
        from types import SimpleNamespace

        service = ProvisioningServiceConfig(
            token_env="GENERATED_TOKEN",  # noqa: S106 — env var name
            sources=frozenset(sources or {"discord"}),
            scopes=tuple(scopes or (
                ProvisioningScopeConfig(kind="unit", id="u"),)))
        return SimpleNamespace(
            identity=SimpleNamespace(
                backend=backend,
                onboarding=OnboardingConfig(collections=collections),
                provisioning={"svc": service}),
            platforms=platforms or SimpleNamespace(
                discord=SimpleNamespace(mode="direct"),
                signal=SimpleNamespace(mode="direct", base_url="",
                                       account="+852111")),
            bridges=SimpleNamespace(
                token_env="GENERATED_BRIDGE"),  # noqa: S106 — env var name
            credentials={})

    def test_file_mode_and_empty_collections_build_nothing(self):
        from nora.app.composition_onboarding import onboarding as build

        services, _ = _graph()
        assert build(
            self._cfg(backend="file"), managed_services=services,
            discord_control=None, discord_token="").onboarding is None
        assert build(
            self._cfg(), managed_services=services,
            discord_control=None, discord_token="").onboarding is None

    def test_managed_collections_build_rules_under_the_service(self):
        from types import SimpleNamespace

        from nora.app.composition_onboarding import onboarding as build

        services, _ = _graph()
        wiring = build(
            self._cfg(collections=(
                OnboardingCollectionConfig(
                    name="g", service="svc", platform="discord",
                    collection=GUILD,
                    scope=ProvisioningScopeConfig(kind="unit", id="u")),
            ), platforms=SimpleNamespace(
                discord=SimpleNamespace(mode="direct"),
                signal=SimpleNamespace(mode="direct", base_url="",
                                       account="+852111"))),
            managed_services=services, discord_control=None,
            discord_token="generated-token")  # noqa: S106 — generated, not real
        onboarding = wiring.onboarding
        assert isinstance(onboarding, PlatformOnboarding)
        rule = next(iter(onboarding._rules.values()))
        assert rule.actor.service_id == "svc"
        assert rule.actor.sources == {"discord"}
        assert isinstance(rule.discovery, DiscordDiscovery)
        assert rule.scope == Scope(ScopeKind.UNIT, "u")

    async def test_bridge_mode_uses_the_control_reads_transport(self):
        from types import SimpleNamespace

        from nora.app.composition_onboarding import onboarding as build
        from nora.platform.bridge.client import BridgeAnswer

        services, _ = _graph()

        class Control:
            def __init__(self):
                self.calls = []

            async def read(self, kind, params):
                self.calls.append((kind, params))
                return BridgeAnswer("ok", 200, {"outcome": "ok", "payload": {"members": [
                    {"user": {"id": "10001"}}]}})

        control = Control()
        wiring = build(
            self._cfg(collections=(
                OnboardingCollectionConfig(
                    name="g", service="svc", platform="discord",
                    collection=GUILD,
                    scope=ProvisioningScopeConfig(kind="unit", id="u")),
            ), platforms=SimpleNamespace(
                discord=SimpleNamespace(mode="bridge"),
                signal=SimpleNamespace(mode="direct", base_url="",
                                       account="+852111"))),
            managed_services=services, discord_control=control,
            discord_token="")
        rule = next(iter(wiring.onboarding._rules.values()))
        snapshot = await rule.discovery.members(collection=GUILD)
        assert control.calls == [("members", {"guild_id": GUILD})]
        assert snapshot.available

    def test_platform_with_no_roster_capability_refuses_at_boot(self):
        from nora.app.composition_onboarding import onboarding as build
        from nora.app.startup import StartupError

        services, _ = _graph()
        with pytest.raises(StartupError):
            build(
                self._cfg(collections=(
                    OnboardingCollectionConfig(
                        name="t", service="svc", platform="telegram",
                        collection="generated-chat",
                        scope=ProvisioningScopeConfig(kind="unit", id="u")),
                ), sources={"telegram"}),
                managed_services=services, discord_control=None,
                discord_token="generated-token")  # noqa: S106 — generated

    def test_room_audience_on_a_guild_collection_refuses_at_boot(self):
        from nora.app.composition_onboarding import onboarding as build
        from nora.app.startup import StartupError

        services, _ = _graph()
        with pytest.raises(StartupError):
            build(
                self._cfg(
                    collections=(
                        OnboardingCollectionConfig(
                            name="g", service="svc", platform="discord",
                            collection=GUILD, audience="room",
                            scope=ProvisioningScopeConfig(
                                kind="room", id="r")),
                    ),
                    scopes=(ProvisioningScopeConfig(kind="room", id="r"),)),
                managed_services=services, discord_control=None,
                discord_token="generated-token")  # noqa: S106 — generated
