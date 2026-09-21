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
"""

from __future__ import annotations

import pytest

from nora.infra.social import EXCEPTION_ROUTES, SocialRouter
from nora.testing import FakeSocialProvider

_STORY = {"platform": "instagram", "what": "story", "target": "miles", "limit": 1}
_PROFILE = {"platform": "instagram", "what": "profile", "target": "miles", "limit": 5}
_X_POST = {"platform": "x", "what": "post", "target": "12345", "limit": 5}


def _router(*, default=None, instagram=None):
    return SocialRouter(
        default=default,
        exceptions={("instagram", "story"): instagram} if instagram else {},
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
    router = SocialRouter(
        default=default, exceptions={("instagram", "story"): _Raises()},
    )

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
