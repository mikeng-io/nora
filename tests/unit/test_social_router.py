"""`SocialRouter`'s own rules — the ones that are about ROUTING rather than
about the port (design/instagram-adoption/08-social-adapter-architecture).

Its port obligations (three states, never raises, no status re-mapping) are in
`tests/contract/test_social_provider_contract.py`, where they are checked
against every implementation. What is here is what only the router can get
wrong: sending a read to the wrong backend, inventing an exception row, and —
the one with teeth — failing over.

Every test asserts WHICH backend was asked, not only what came back. A router
that returned the right status from the wrong backend would pass a
status-only test while answering a different question from the one it was
given, which is exactly the failure the design forbids in those words: *a
router that improvises is a router that lies about what it looked at.*

The second half of this file is the TIER LADDER, which moved here from
`InstagramSocialClient` on 2026-09-22 (design/instagram-adoption/10 §"Who
owns what"). It is tested here for the reason it moved: inside one backend it
could only ever govern that backend's one row, and the other seven had no
subject authorization at all. So the assertions are about TOTALITY and about
the negative — that a Tier 2 row is never resolved through the index — as
much as about the two refusal statuses, because "nothing was looked up" is
invisible in a status and an unclassified row is invisible in a green suite.
"""

from __future__ import annotations

import pytest

from nora.infra.social import (
    EXCEPTION_ROUTES,
    SUBJECT_TIERS,
    TIER_NAMED,
    TIER_ROOM,
    SocialRouter,
)
from nora.infra.social.bridge_server import READ_ONLY_ALLOWLIST
from nora.testing import FakeSocialProvider, FakeSocialSubjectIndex

_STORY = {"platform": "instagram", "what": "story", "target": "miles", "limit": 1}
_PROFILE = {"platform": "instagram", "what": "profile", "target": "miles", "limit": 5}
_X_POST = {"platform": "x", "what": "post", "target": "12345", "limit": 5}
#: `miles` resolves to this igid and is in a room Nora is in — Tier 0, the only
#: tier v1 serves. `_STORY`'s `target` is the PERSON id; the igid is what the
#: backend must be handed instead.
_SUBJECTS = {"miles": "221731317"}


def _router(*, default=None, instagram=None, subjects=None):
    return SocialRouter(
        default=default,
        exceptions={("instagram", "story"): instagram} if instagram else {},
        subjects=(
            subjects if subjects is not None
            else (FakeSocialSubjectIndex(_SUBJECTS) if instagram else None)
        ),
    )


def test_the_exception_list_has_exactly_the_row_the_design_records():
    """Pinned as a VALUE, not a shape. The list is short on purpose and every
    row carries a proof that the default backend structurally cannot serve it;
    a row appearing here without that proof is the drift the rule exists to
    stop, and it should cost somebody an edit to this line and a reason."""
    assert frozenset({("instagram", "story")}) == EXCEPTION_ROUTES


async def test_everything_outside_the_exception_list_goes_to_the_default():
    """The axis split is the RULE. `instagram/profile` and `instagram/posts`
    are Instagram verbs and still belong to OpenCLI — "it is the same network"
    is not a routing argument, and treating it as one is how a one-row
    exception list becomes two parallel integrations."""
    default = FakeSocialProvider({
        ("instagram", "profile"): {"status": "ok", "text": "bio"},
        ("x", "post"): {"status": "ok", "text": "a post"},
    })
    instagram = FakeSocialProvider({("instagram", "story"): {"status": "ok"}})
    router = _router(default=default, instagram=instagram)

    assert (await router.read(**_PROFILE))["status"] == "ok"
    assert (await router.read(**_X_POST))["status"] == "ok"

    assert [c[:2] for c in default.calls] == [("instagram", "profile"), ("x", "post")]
    assert instagram.calls == []


async def test_the_one_exception_row_goes_to_the_second_backend():
    default = FakeSocialProvider({("instagram", "profile"): {"status": "ok"}})
    instagram = FakeSocialProvider({
        ("instagram", "story"): {"status": "ok", "items": [], "text": ""},
    })
    router = _router(default=default, instagram=instagram)

    assert (await router.read(**_STORY))["status"] == "ok"
    assert [c[:2] for c in instagram.calls] == [("instagram", "story")]
    assert default.calls == []


async def test_an_unavailable_exception_backend_is_not_failed_over_to_the_default():
    """THE rule. When the Instagram session is checkpointed the answer is
    `unavailable` — never a quiet profile read that answers a different
    question. The default backend has no story read at all, so a failover
    could not even produce the right KIND of answer; it would produce a
    confident one about something else, and the model has no way to tell.

    The assertion that matters is `default.calls == []`: a status-only check
    would pass on a router that asked OpenCLI, got a refusal, and reported
    `unavailable` — the right word from the wrong act."""
    default = FakeSocialProvider({("instagram", "profile"): {"status": "ok"}})
    instagram = FakeSocialProvider(unavailable=True)
    router = _router(default=default, instagram=instagram)

    out = await router.read(**_STORY)

    assert out["status"] == "unavailable"
    assert default.calls == []
    assert len(instagram.calls) == 1   # positive control: it DID try the one backend


async def test_an_unwired_exception_backend_does_not_fall_through_either():
    """The same rule in its cheaper disguise. Writing the dispatch as
    `exceptions.get(key, default)` would be one character shorter and would
    silently route every story read to OpenCLI whenever the Instagram backend
    was not composed — which is the state the deployment is in TODAY, so this
    is not a hypothetical: it is the branch production takes."""
    default = FakeSocialProvider({("instagram", "profile"): {"status": "ok"}})
    router = _router(default=default, instagram=None)

    out = await router.read(**_STORY)

    assert out["status"] == "unavailable"
    assert default.calls == []


async def test_an_unwired_default_does_not_reach_the_exception_backend():
    """And the mirror image, so the isolation is proved in both directions:
    `[social] enabled = false` must not turn the Instagram backend into a
    general-purpose social reader. It serves one row."""
    instagram = FakeSocialProvider({("instagram", "story"): {"status": "ok"}})
    router = _router(default=None, instagram=instagram)

    assert (await router.read(**_PROFILE))["status"] == "unavailable"
    assert instagram.calls == []


async def test_a_raising_backend_becomes_unavailable_and_still_does_not_fail_over():
    """R-5's backstop, checked where it is most tempting to be clever: an
    implementation that raises is exactly the case where "just try the other
    one" looks like resilience."""

    class _Raises:
        async def read(self, *, platform, what, target, limit):
            raise RuntimeError("boom")

    default = FakeSocialProvider({("instagram", "profile"): {"status": "ok"}})
    router = _router(default=default, instagram=_Raises())

    assert (await router.read(**_STORY))["status"] == "unavailable"
    assert default.calls == []


def test_a_row_the_exception_list_does_not_carry_is_refused_at_construction():
    """A composition root cannot invent an exception row. The list is a design
    artifact carrying a proof of impossibility per entry; a wiring line that
    could add one would move the routing rule out of the place it is reviewed.

    It fails at BOOT rather than at read time on purpose — a mis-wired router
    that only misbehaves on one rare verb is worse than one that refuses to
    start, because the first is discovered by a wrong answer in a room."""
    with pytest.raises(ValueError, match="exception list"):
        SocialRouter(
            default=None,
            exceptions={("instagram", "profile"): FakeSocialProvider()},
        )


# ---- the tier ladder -------------------------------------------------------
#
# Moved here from `InstagramSocialClient` (design/instagram-adoption/10 §"Who
# owns what"). The two refusal statuses are the visible part; the part that
# actually changed is that the table is TOTAL, so the seven default rows have
# an authorization decision instead of an absence.


def test_every_row_a_backend_can_serve_names_its_tier():
    """TOTALITY, derived — never hand-listed.

    The whole defect this change fixes is a rule that reached one row of
    eight. A tier table with a gap in it is that defect again, quieter: the
    missing row would simply not be authorized, and nothing would say so. So
    the key set is derived from the two places a pair can be served from — the
    default backend's own allowlist and the router's exception list — and a
    verb added to either without a tier goes red HERE, before it can be read
    unauthorized in a room.
    """
    servable = set(READ_ONLY_ALLOWLIST) | EXCEPTION_ROUTES
    assert set(SUBJECT_TIERS) == servable
    # And the split is the one node 07 describes: the index-resolved story row
    # is Tier 0, every model-supplied handle/URL/query row is Tier 2.
    assert SUBJECT_TIERS[("instagram", "story")] == TIER_ROOM
    assert {row for row, tier in SUBJECT_TIERS.items() if tier == TIER_NAMED} == set(
        READ_ONLY_ALLOWLIST
    )


async def test_a_tier_two_row_is_allowed_and_is_never_resolved_through_the_index():
    """Tier 2 is «allowed, never resolved implicitly» (`07`), and the second
    clause is the one code below the tool can hold.

    Whether a human typed `nasa` is a fact about the conversation that this
    layer cannot see. Whether NORA turned a name into a handle is a fact about
    this function — so the measurable property is that the subject index is
    not consulted at all, and that the `target` reaches the backend byte for
    byte. A status-only assertion is blind to both: a router that looked
    `nasa` up in the person index, found nothing, and read it anyway would
    return exactly this `ok`.
    """
    subjects = FakeSocialSubjectIndex(_SUBJECTS)
    default = FakeSocialProvider({
        ("x", "post"): {"status": "ok"},
        ("instagram", "profile"): {"status": "ok"},
    })
    router = _router(default=default, subjects=subjects)

    assert (await router.read(**_X_POST))["status"] == "ok"
    assert (await router.read(**_PROFILE))["status"] == "ok"

    assert subjects.asked == []
    assert [c[2] for c in default.calls] == ["12345", "miles"]


async def test_a_tier_zero_row_reaches_the_backend_as_the_RESOLVED_subject():
    """The router asks the person index; the backend is handed what came back.

    `target` arrives as a `person_id` and must leave as an igid — that is the
    resolution step, and asserting the VALUE is what makes it a test rather
    than a shape check. A router that authorized `miles` and then forwarded the
    string `miles` would be green on every status while the bridge was handed a
    person id to guess at.
    """
    instagram = FakeSocialProvider({("instagram", "story"): {"status": "ok"}})
    router = _router(instagram=instagram)

    assert (await router.read(**_STORY))["status"] == "ok"
    assert instagram.calls == [("instagram", "story", "221731317", 1)]


async def test_an_unresolved_subject_is_ambiguous_and_nothing_is_dispatched():
    """«I don't know which account that is» — NOT a handle guessed from the
    name, and not `nothing_found`, because nothing was looked at. The backend
    never being called is the assertion that says the read did not happen; a
    status alone cannot tell a refusal apart from a read that found nothing."""
    instagram = FakeSocialProvider({("instagram", "story"): {"status": "ok"}})
    router = _router(instagram=instagram, subjects=FakeSocialSubjectIndex())

    assert (await router.read(**_STORY))["status"] == "ambiguous"
    assert instagram.calls == []


async def test_an_out_of_tier_subject_is_refused_and_nothing_is_dispatched():
    """v1 is Tier 0 only. «I may not look him up» and «I couldn't look» are
    different claims — one is a boundary the operator set, the other an outage
    somebody should fix — so `refused` must not collapse into `unavailable`,
    and the read must not happen."""
    instagram = FakeSocialProvider({("instagram", "story"): {"status": "ok"}})
    router = _router(
        instagram=instagram,
        subjects=FakeSocialSubjectIndex(_SUBJECTS, out_of_reach=frozenset({"miles"})),
    )

    assert (await router.read(**_STORY))["status"] == "refused"
    assert instagram.calls == []


async def test_an_index_that_cannot_be_read_is_never_a_read():
    """The port fails CLOSED and so does this: an index that answers nothing
    costs a read that should have happened rather than granting one that
    should not have. It lands on `ambiguous` — «I don't know whose account»,
    which is true of an index nobody could read — and never on a dispatch.
    Pinned as ONE status, not a set: «either of these is fine» is how a
    fail-closed path quietly stops being one."""
    instagram = FakeSocialProvider({("instagram", "story"): {"status": "ok"}})
    router = _router(
        instagram=instagram,
        subjects=FakeSocialSubjectIndex(_SUBJECTS, unavailable=True),
    )

    assert (await router.read(**_STORY))["status"] == "ambiguous"
    assert instagram.calls == []


async def test_a_raising_index_does_not_become_a_read():
    """The port says neither method raises; this is the belt for those braces,
    checked where it is most dangerous. A raised exception that fell through
    to the dispatch would turn a broken authorization step into an
    unauthorized read — the one failure mode where «carry on» is worst."""

    class _RaisingIndex:
        async def igid_for(self, *, person_id):
            raise RuntimeError("index on fire")

        async def is_tier_zero(self, *, person_id):
            raise RuntimeError("index on fire")

    instagram = FakeSocialProvider({("instagram", "story"): {"status": "ok"}})
    router = _router(instagram=instagram, subjects=_RaisingIndex())

    assert (await router.read(**_STORY))["status"] == "unavailable"
    assert instagram.calls == []


async def test_a_row_with_no_tier_is_not_served():
    """The fail-closed direction of the table being total. A pair that reaches
    a backend without a tier is a vocabulary that grew without an
    authorization decision, so it is `unavailable` — «I couldn't look», which
    is true of a wiring gap — and never `refused`, which would report a
    developer's omission as a boundary the operator set.

    `x/story` is the pair used because it is REAL: it is inside the tool's
    (platform, what) product, has no backend, and is declared unsupported one
    layer up. If that declaration were ever dropped, this is the behaviour
    that catches it.
    """
    default = FakeSocialProvider({("x", "story"): {"status": "ok"}})
    router = _router(default=default, subjects=FakeSocialSubjectIndex(_SUBJECTS))

    out = await router.read(platform="x", what="story", target="nasa", limit=5)

    assert out["status"] == "unavailable"
    assert default.calls == []   # it was not served, not merely not answered


def test_a_tier_zero_backend_cannot_be_wired_without_a_subject_index():
    """Boot-time, for the same reason the exception list is: the only two
    fallbacks for «Tier 0 row, no index» are allow — an unauthorized read —
    and deny, which reports a wiring gap as a boundary the operator set.
    Neither is a thing to discover from a wrong answer in a room."""
    with pytest.raises(ValueError, match="Tier 0"):
        SocialRouter(
            default=None,
            exceptions={("instagram", "story"): FakeSocialProvider()},
            subjects=None,
        )


def test_the_default_backend_alone_needs_no_index():
    """The mirror image, and the state production is in TODAY: every row the
    OpenCLI backend serves is Tier 2, so `[social] enabled` on its own must
    compose without a person index. A check that demanded one unconditionally
    would have turned this change into an outage on the seven rows it was
    supposed to protect."""
    router = SocialRouter(default=FakeSocialProvider(), subjects=None)
    assert isinstance(router, SocialRouter)
