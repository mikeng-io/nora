"""`SocialRouter`'s own rules — the ones that are about ROUTING rather than
about the port.

Its port obligations (three states, never raises, no status re-mapping) are in
`tests/contract/test_social_provider_contract.py`, where they are checked
against every implementation. What is here is what only the router can get
wrong: sending a read to the wrong backend, inventing an exception row, and —
the one with teeth — failing over.

Every test asserts WHICH backend was asked, not only what came back. A router
that returned the right status from the wrong backend would pass a
status-only test while answering a different question from the one it was
given: *a router that improvises is a router that lies about what it looked at.*

The second half of this file is RESOLUTION and REACH, keyed separately:
resolution by `(platform, what, input kind)`, reach by the read alone. They are
enforced in the router because inside one backend they could only govern that
backend's rows. So the assertions are about TOTALITY and about the negatives —
that a `named` target is never resolved through the index, and that a profile
is never reach-checked — as much as about the two refusal statuses, because
"nothing was looked up" is invisible in a status and an unclassified row is
invisible in a green suite.
"""

from __future__ import annotations

import pytest

from nora.infra.social import (
    EXCEPTION_ROUTES,
    SUBJECT_INPUTS,
    TARGET_HANDLE,
    TARGET_NAMED,
    TARGET_PERSON,
    TIER_ZERO_READS,
    SocialRouter,
)
from nora.infra.social.bridge_server import READ_ONLY_ALLOWLIST
from nora.testing import (
    FakeInstagramAccountLookup,
    FakeSocialProvider,
    FakeSocialSubjectIndex,
)

#: Both Instagram rows arrive as a PERSON — the tool resolved a spoken name to
#: this person id inside the room.
_STORY = {
    "platform": "instagram", "what": "story", "target": "miles", "limit": 1,
    "target_kind": TARGET_PERSON,
}
_PROFILE = {**_STORY, "what": "profile", "limit": 5}
_X_POST = {"platform": "x", "what": "post", "target": "12345", "limit": 5}
_IG_SEARCH = {"platform": "instagram", "what": "search", "target": "hk hiking", "limit": 5}
#: `miles` resolves to this igid and is in a room Nora is in — Tier 0.
#: `_STORY`'s `target` is the PERSON id; the igid is what the backend must be
#: handed instead. Synthetic.
_SUBJECTS = {"miles": "10000001"}


def _router(*, default=None, instagram=None, subjects=None):
    return SocialRouter(
        default=default,
        exceptions=dict.fromkeys(EXCEPTION_ROUTES, instagram) if instagram else {},
        subjects=(
            subjects if subjects is not None
            else (FakeSocialSubjectIndex(_SUBJECTS) if instagram else None)
        ),
    )


def test_the_exception_list_has_exactly_the_rows_the_design_records():
    """Pinned as a VALUE, not a shape. The list is the Instagram rows the
    bridge serves END TO END; a row joins it only when its OpenCLI row is
    removed. A row appearing here otherwise should cost somebody an edit to
    this line and a reason."""
    assert frozenset({
        ("instagram", "story"), ("instagram", "profile"), ("instagram", "search"),
        ("instagram", "post"),
    }) == EXCEPTION_ROUTES


async def test_x_goes_to_the_default_and_instagram_search_to_the_bridge():
    """`instagram/search` is served by the bridge, so OpenCLI serves X only. The
    default is offered an Instagram search here and must never be asked: a
    router that sent it there would be two backends for one read."""
    default = FakeSocialProvider({
        ("instagram", "search"): {"status": "ok", "text": "accounts"},
        ("x", "post"): {"status": "ok", "text": "a post"},
    })
    instagram = FakeSocialProvider({("instagram", "search"): {"status": "ok"}})
    router = _router(default=default, instagram=instagram)

    assert (await router.read(**_IG_SEARCH))["status"] == "ok"
    assert (await router.read(**_X_POST))["status"] == "ok"

    assert [c[:2] for c in default.calls] == [("x", "post")]
    assert [c[:3] for c in instagram.calls] == [("instagram", "search", "hk hiking")]


async def test_both_instagram_rows_go_to_the_bridge_backend():
    default = FakeSocialProvider({
        ("instagram", "profile"): {"status": "ok"},
        ("instagram", "story"): {"status": "ok"},
    })
    instagram = FakeSocialProvider({
        ("instagram", "story"): {"status": "ok", "items": [], "text": ""},
        ("instagram", "profile"): {"status": "ok", "text": ""},
    })
    router = _router(default=default, instagram=instagram)

    assert (await router.read(**_STORY))["status"] == "ok"
    assert (await router.read(**_PROFILE))["status"] == "ok"
    assert [c[:2] for c in instagram.calls] == [
        ("instagram", "story"), ("instagram", "profile"),
    ]
    assert default.calls == []


async def test_an_unavailable_exception_backend_is_not_failed_over_to_the_default():
    """THE rule. When the Instagram session is checkpointed the answer is
    `unavailable` — never a quiet read on the other backend that answers a
    different question.

    The assertion that matters is `default.calls == []`: a status-only check
    would pass on a router that asked OpenCLI, got a refusal, and reported
    `unavailable` — the right word from the wrong act."""
    default = FakeSocialProvider({("instagram", "story"): {"status": "ok"}})
    instagram = FakeSocialProvider(unavailable=True)
    router = _router(default=default, instagram=instagram)

    out = await router.read(**_STORY)

    assert out["status"] == "unavailable"
    assert default.calls == []
    assert len(instagram.calls) == 1   # positive control: it DID try the one backend


async def test_an_unwired_exception_backend_does_not_fall_through_either():
    """The same rule in its cheaper disguise. Writing the dispatch as
    `exceptions.get(key, default)` would be one character shorter and would
    silently route every Instagram read to OpenCLI whenever the Instagram
    backend was not composed."""
    default = FakeSocialProvider({("instagram", "story"): {"status": "ok"}})
    router = _router(default=default, instagram=None)

    out = await router.read(**_STORY)

    assert out["status"] == "unavailable"
    assert default.calls == []


async def test_an_unwired_backend_answers_one_stable_sentence_and_logs_nothing_per_call(
    caplog,
):
    """A backend that is not composed is CONFIGURATION — its flag is off — so
    the answer is the same every time and says what is true («Instagram isn't
    set up»), not «I couldn't read that just now», which invites a retry that
    cannot help. It is said once at boot, never as a warning per question."""
    with caplog.at_level("DEBUG", logger="nora.social.router"):
        router = _router(default=FakeSocialProvider(), instagram=None)
        # Positive control: the unwired rows ARE reported — once, at boot.
        assert [r.getMessage() for r in caplog.records
                if r.name == "nora.social.router"] != []
        caplog.clear()
        first = await router.read(**_STORY)
        second = await router.read(**_PROFILE)

    assert first == second
    assert first["status"] == "unavailable"
    assert "Instagram isn't set up" in first["note"]
    assert "just now" not in first["note"]
    assert [r for r in caplog.records if r.name == "nora.social.router"] == []


async def test_an_unwired_default_does_not_reach_the_exception_backend():
    """And the mirror image, so the isolation is proved in both directions:
    `[social] enabled = false` must not turn the Instagram backend into a
    general-purpose social reader. It serves its own rows only."""
    instagram = FakeSocialProvider({("x", "post"): {"status": "ok"}})
    router = _router(default=None, instagram=instagram)

    assert (await router.read(**_X_POST))["status"] == "unavailable"
    assert instagram.calls == []


async def test_a_raising_backend_becomes_unavailable_and_still_does_not_fail_over():
    """R-5's backstop, checked where it is most tempting to be clever: an
    implementation that raises is exactly the case where "just try the other
    one" looks like resilience."""

    class _Raises:
        async def read(self, *, platform, what, target, limit):
            raise RuntimeError("boom")

    default = FakeSocialProvider({("instagram", "story"): {"status": "ok"}})
    router = _router(default=default, instagram=_Raises())

    assert (await router.read(**_STORY))["status"] == "unavailable"
    assert default.calls == []


def test_a_row_the_exception_list_does_not_carry_is_refused_at_construction():
    """A composition root cannot invent an exception row. The list is a design
    artifact; a wiring line that could add one would move the routing rule out
    of the place it is reviewed.

    It fails at BOOT rather than at read time on purpose — a mis-wired router
    that only misbehaves on one rare verb is worse than one that refuses to
    start, because the first is discovered by a wrong answer in a room."""
    with pytest.raises(ValueError, match="exception list"):
        SocialRouter(
            default=None,
            exceptions={("x", "post"): FakeSocialProvider()},
        )


# ---- resolution and reach --------------------------------------------------
#
# Resolution is keyed on `(platform, what, input kind)`, reach on the read alone.


def test_every_row_a_backend_can_serve_says_how_its_subject_resolves():
    """TOTALITY, derived — never hand-listed.

    A resolution table with a gap in it is the defect this module exists to
    prevent, quieter: the missing row would simply not be authorized, and
    nothing would say so. So the servable set is derived from the two places a
    pair can be served from — the default backend's own allowlist and the
    exception list — and a verb added to either without a resolution row goes
    red HERE, before it can be read unauthorized in a room.
    """
    servable = set(READ_ONLY_ALLOWLIST) | EXCEPTION_ROUTES
    assert {(platform, what) for platform, what, _kind in SUBJECT_INPUTS} == servable
    # Every OpenCLI row takes a string a human typed; the bridge's profile and
    # story take a PERSON or a HANDLE (typed, or a link's username), never a
    # bare string; the bridge's SEARCH takes the free text a person typed and
    # its POST the shortcode of a pasted link, neither ever resolved.
    named = {(p, w) for p, w, k in SUBJECT_INPUTS if k == TARGET_NAMED}
    person = {(p, w) for p, w, k in SUBJECT_INPUTS if k == TARGET_PERSON}
    handle = {(p, w) for p, w, k in SUBJECT_INPUTS if k == TARGET_HANDLE}
    assert named == set(READ_ONLY_ALLOWLIST) | {("instagram", "search"), ("instagram", "post")}
    assert person == {("instagram", "profile"), ("instagram", "story")}
    assert handle == person
    assert not (named & person), "a bridge read took both a bare string and a person"
    # Reach is keyed on the read, and only a story has one.
    assert frozenset({("instagram", "story")}) == TIER_ZERO_READS


async def test_a_named_row_is_allowed_and_is_never_resolved_through_the_index():
    """Tier 2 is «allowed, never resolved implicitly», and the second
    clause is the one code below the tool can hold.

    Whether a human typed `nasa` is a fact about the conversation that this
    layer cannot see. Whether NORA turned a name into a handle is a fact about
    this function — so the measurable property is that the subject index is
    not consulted at all, and that the `target` reaches the backend byte for
    byte. A status-only assertion is blind to both.
    """
    subjects = FakeSocialSubjectIndex(_SUBJECTS)
    default = FakeSocialProvider({("x", "post"): {"status": "ok"}})
    instagram = FakeSocialProvider({("instagram", "search"): {"status": "ok"}})
    router = _router(default=default, instagram=instagram, subjects=subjects)

    assert (await router.read(**_X_POST))["status"] == "ok"
    assert (await router.read(**_IG_SEARCH))["status"] == "ok"

    assert subjects.asked == []
    assert [c[2] for c in default.calls] == ["12345"]
    assert [c[2] for c in instagram.calls] == ["hk hiking"]


async def test_a_person_row_reaches_the_backend_as_the_RESOLVED_subject():
    """The router asks the person index; the backend is handed what came back.

    `target` arrives as a `person_id` and must leave as an igid — that is the
    resolution step, and asserting the VALUE is what makes it a test rather
    than a shape check. A router that authorized `miles` and then forwarded the
    string `miles` would be green on every status while the bridge was handed a
    person id to guess at.
    """
    instagram = FakeSocialProvider({
        ("instagram", "story"): {"status": "ok"},
        ("instagram", "profile"): {"status": "ok"},
    })
    router = _router(instagram=instagram)

    assert (await router.read(**_STORY))["status"] == "ok"
    assert (await router.read(**_PROFILE))["status"] == "ok"
    assert instagram.calls == [
        ("instagram", "story", "10000001", 1),
        ("instagram", "profile", "10000001", 5),
    ]


async def test_a_profile_has_no_reach_check():
    """Reach is keyed on the READ. Only a story has a reach check (a privacy
    choice: Nora opens only listed people's stories), so a profile needs
    resolution alone; refusing it for an out-of-reach person would be a
    boundary nobody set. The same person's story is refused — the control
    that says the index DID say «out of reach»."""
    instagram = FakeSocialProvider({
        ("instagram", "story"): {"status": "ok"},
        ("instagram", "profile"): {"status": "ok"},
    })
    router = _router(
        instagram=instagram,
        subjects=FakeSocialSubjectIndex(_SUBJECTS, out_of_reach=frozenset({"miles"})),
    )

    assert (await router.read(**_PROFILE))["status"] == "ok"
    assert (await router.read(**_STORY))["status"] == "refused"
    assert [c[:2] for c in instagram.calls] == [("instagram", "profile")]


@pytest.mark.parametrize("read", [_STORY, _PROFILE], ids=["story", "profile"])
async def test_a_typed_handle_on_a_bridge_row_is_not_served(read):
    """A `named` target on an Instagram profile or story row is not
    admitted, even with the username lookup: a handle must arrive AS a
    `handle`, which the lookup resolves and the reach check covers. A bare
    `named` string would be a handle nobody resolved, and for a story it would
    skip the reach check entirely (a handle names no person to check).
    `unavailable`, and nothing is dispatched or looked up."""
    subjects = FakeSocialSubjectIndex(_SUBJECTS)
    instagram = FakeSocialProvider({row: {"status": "ok"} for row in EXCEPTION_ROUTES})
    router = _router(instagram=instagram, subjects=subjects)

    out = await router.read(
        **{**read, "target": "example_friend", "target_kind": TARGET_NAMED},
    )

    assert out["status"] == "unavailable"
    assert instagram.calls == []
    assert subjects.asked == []


async def test_a_person_on_a_named_row_is_not_served():
    """The mirror image: an X read is keyed on a string a human typed, and a
    person id arriving there would be Nora turning a name into a handle."""
    default = FakeSocialProvider({("x", "profile"): {"status": "ok"}})
    router = _router(default=default, subjects=FakeSocialSubjectIndex(_SUBJECTS))

    out = await router.read(
        platform="x", what="profile", target="miles", limit=5, target_kind=TARGET_PERSON,
    )
    assert out["status"] == "unavailable"
    assert default.calls == []


async def test_the_default_input_kind_is_named():
    """The X tools call the port's four-argument `read`; the router's
    extra argument defaults to the port's own meaning, so that call is
    unchanged — and an Instagram bridge row reached that way is refused
    rather than resolved."""
    instagram = FakeSocialProvider({("instagram", "story"): {"status": "ok"}})
    router = _router(instagram=instagram)

    legacy = {k: v for k, v in _STORY.items() if k != "target_kind"}
    assert (await router.read(**legacy))["status"] == "unavailable"
    assert instagram.calls == []


async def test_no_account_on_file_is_nothing_found_and_nothing_is_dispatched():
    """The index was read and holds no igid for them: «no account on file, so
    I didn't look» — `nothing_found` with a note that says so, NOT a handle
    guessed from the name and NOT `ambiguous`, whose sentence is about
    choosing between accounts that exist. The backend never being called is
    what says no read happened."""
    instagram = FakeSocialProvider({row: {"status": "ok"} for row in EXCEPTION_ROUTES})
    router = _router(instagram=instagram, subjects=FakeSocialSubjectIndex())

    for read in (_STORY, _PROFILE):
        out = await router.read(**read)
        assert out["status"] == "nothing_found"
        assert "on file" in out["note"]
    assert instagram.calls == []


async def test_several_accounts_on_file_is_ambiguous_and_never_a_pick():
    """Two igids for one person and nothing to say which was meant. The note
    tells the model to say so, NOT to ask: the person in the room cannot
    settle it, only the operator's file can."""
    instagram = FakeSocialProvider({row: {"status": "ok"} for row in EXCEPTION_ROUTES})
    router = _router(
        instagram=instagram,
        subjects=FakeSocialSubjectIndex({"miles": frozenset({"10000001", "10000002"})}),
    )

    for read in (_STORY, _PROFILE):
        out = await router.read(**read)
        assert out["status"] == "ambiguous"
        assert "more than one account" in out["note"]
        assert "ask" not in out["note"].lower()
    assert instagram.calls == []


async def test_an_out_of_tier_subject_is_refused_and_nothing_is_dispatched():
    """«I may not look him up» and «I couldn't look» are different claims —
    one is a boundary the operator set, the other an outage somebody should
    fix — so `refused` must not collapse into `unavailable`, and the read
    must not happen."""
    instagram = FakeSocialProvider({("instagram", "story"): {"status": "ok"}})
    router = _router(
        instagram=instagram,
        subjects=FakeSocialSubjectIndex(_SUBJECTS, out_of_reach=frozenset({"miles"})),
    )

    assert (await router.read(**_STORY))["status"] == "refused"
    assert instagram.calls == []


async def test_an_index_that_cannot_be_read_is_unavailable_and_never_a_read():
    """The port fails CLOSED and so does this: an index that could not be read
    (`None`) costs a read that should have happened rather than granting one
    that should not have. It is `unavailable` — «I couldn't check» — and not
    `ambiguous` or `nothing_found`, which are claims about the person's
    accounts that nobody could make. Pinned as ONE status:
    «either of these is fine» is how a fail-closed path quietly stops being one."""
    instagram = FakeSocialProvider({("instagram", "story"): {"status": "ok"}})
    router = _router(
        instagram=instagram,
        subjects=FakeSocialSubjectIndex(_SUBJECTS, unavailable=True),
    )

    assert (await router.read(**_STORY))["status"] == "unavailable"
    assert instagram.calls == []


async def test_a_raising_index_does_not_become_a_read():
    """The port says neither method raises; this is the belt for those braces,
    checked where it is most dangerous. A raised exception that fell through
    to the dispatch would turn a broken authorization step into an
    unauthorized read — the one failure mode where «carry on» is worst."""

    class _RaisingIndex:
        async def igids_for(self, *, person_id):
            raise RuntimeError("index on fire")

        async def is_tier_zero(self, *, person_id):
            raise RuntimeError("index on fire")

        async def person_for_igid(self, *, igid):
            raise RuntimeError("index on fire")

    instagram = FakeSocialProvider({("instagram", "story"): {"status": "ok"}})
    router = _router(instagram=instagram, subjects=_RaisingIndex())

    assert (await router.read(**_STORY))["status"] == "unavailable"
    assert instagram.calls == []


async def test_a_row_with_no_resolution_is_not_served():
    """The fail-closed direction of the table being total. A pair that reaches
    a backend without a resolution row is a vocabulary that grew without an
    authorization decision, so it is `unavailable` — «I couldn't look», which
    is true of a wiring gap — and never `refused`, which would report a
    developer's omission as a boundary the operator set.

    `x/story` is used because it is REAL: X has no story concept, and no tool
    constructs it. If one ever did, this is the behaviour that catches it.
    """
    default = FakeSocialProvider({("x", "story"): {"status": "ok"}})
    router = _router(default=default, subjects=FakeSocialSubjectIndex(_SUBJECTS))

    out = await router.read(platform="x", what="story", target="nasa", limit=5)

    assert out["status"] == "unavailable"
    assert default.calls == []   # it was not served, not merely not answered


@pytest.mark.parametrize(
    "row", sorted({(p, w) for p, w, k in SUBJECT_INPUTS if k == TARGET_PERSON}),
)
def test_a_person_keyed_backend_cannot_be_wired_without_a_subject_index(row):
    """Boot-time, for the same reason the exception list is: the only two
    fallbacks for «person row, no index» are allow — an unauthorized read, or
    a person id handed to the bridge as if it were an account — and deny,
    which reports a wiring gap as a boundary the operator set. Each bridge
    row on its own requires one: the profile row for resolution, the story
    row for resolution AND reach."""
    with pytest.raises(ValueError, match="Tier 0"):
        SocialRouter(
            default=None,
            exceptions={row: FakeSocialProvider()},
            subjects=None,
        )


def test_the_post_row_alone_needs_no_index():
    """`instagram/post` takes the shortcode of a pasted link: no
    person resolved and no reach checked, so like the search row it composes
    without an index."""
    router = SocialRouter(
        default=None,
        exceptions={("instagram", "post"): FakeSocialProvider()},
        subjects=None,
    )
    assert isinstance(router, SocialRouter)


def test_the_search_row_alone_needs_no_index():
    """`instagram/search` is a bridge row with a `named` input: free text, no
    person resolved and no reach checked, so wiring it needs no index. The
    constructor's check is DERIVED from the two tables; a hand-listed «every
    bridge row needs an index» would refuse this and read as safe."""
    router = SocialRouter(
        default=None,
        exceptions={("instagram", "search"): FakeSocialProvider()},
        subjects=None,
    )
    assert isinstance(router, SocialRouter)


def test_the_default_backend_alone_needs_no_index():
    """The mirror image: every row the OpenCLI backend serves takes a typed
    string, so `[social] enabled` on its own must compose without a person
    index. A check that demanded one unconditionally would turn X into an
    outage for a feature it does not use."""
    router = SocialRouter(default=FakeSocialProvider(), subjects=None)
    assert isinstance(router, SocialRouter)


# ---- a handle or a link's username -----------------------------------
#
# Resolved by the bridge's EXACT username lookup, never the person index
# (`identity.toml`'s username is display metadata). A story's
# reach check then runs on the igid the handle NAMED, reversed to a person,
# never re-derived through `igids_for`.

_ROBIN_A, _ROBIN_B = "10000002", "10000003"
_HANDLES = {
    "example_friend": "10000001",   # miles's one account
    "robin.personal": _ROBIN_A,      # robin has two
    "robin.work": _ROBIN_B,
    "some.stranger": "10000099",     # nobody in the file
}


def _handle_router(*, subjects=None, accounts=None):
    instagram = FakeSocialProvider({row: {"status": "ok"} for row in EXCEPTION_ROUTES})
    router = SocialRouter(
        default=None,
        exceptions=dict.fromkeys(EXCEPTION_ROUTES, instagram),
        subjects=subjects if subjects is not None else FakeSocialSubjectIndex(
            {"miles": "10000001", "robin": frozenset({_ROBIN_A, _ROBIN_B})},
        ),
        accounts=accounts if accounts is not None else FakeInstagramAccountLookup(_HANDLES),
    )
    return router, instagram


#: The asking room's roster: everyone in the fake index is on it, so the
#: roster check passes unless a test narrows it.
_HERE = frozenset({"miles", "robin"})


def _by_handle(what: str, username: str, room_people=_HERE) -> dict:
    return {
        "platform": "instagram", "what": what, "target": username, "limit": 5,
        "target_kind": TARGET_HANDLE, "room_people": room_people,
    }


async def test_a_handle_profile_reaches_the_backend_as_the_looked_up_igid():
    """The VALUE the backend is handed is the igid the lookup answered, and
    the person index is never consulted for a profile: no reach check, and
    no name was resolved."""
    subjects = FakeSocialSubjectIndex({"miles": "10000001"})
    accounts = FakeInstagramAccountLookup(_HANDLES)
    router, instagram = _handle_router(subjects=subjects, accounts=accounts)

    assert (await router.read(**_by_handle("profile", "some.stranger")))["status"] == "ok"
    assert instagram.calls == [("instagram", "profile", "10000099", 5)]
    assert accounts.asked == ["some.stranger"]
    assert subjects.asked == []


async def test_a_handle_nobody_holds_is_nothing_found_and_nothing_is_dispatched():
    router, instagram = _handle_router()
    out = await router.read(**_by_handle("profile", "no.such.account"))

    assert out["status"] == "nothing_found"
    assert "no account with that username" in out["note"]
    assert instagram.calls == []


@pytest.mark.parametrize("what", ["profile", "story"])
async def test_a_lookup_that_cannot_be_made_is_unavailable_never_a_miss(what):
    router, instagram = _handle_router(
        accounts=FakeInstagramAccountLookup(_HANDLES, unavailable=True),
    )
    out = await router.read(**_by_handle(what, "example_friend"))

    assert out["status"] == "unavailable"
    assert instagram.calls == []


async def test_a_handle_with_no_lookup_composed_is_unavailable():
    """Not a boot error: without the lookup a handle resolves to nothing, so
    nothing is read; the `person` path of the same rows keeps working."""
    instagram = FakeSocialProvider({row: {"status": "ok"} for row in EXCEPTION_ROUTES})
    router = _router(instagram=instagram)

    out = await router.read(**_by_handle("profile", "example_friend"))
    assert out["status"] == "unavailable"
    assert instagram.calls == []
    assert (await router.read(**_PROFILE))["status"] == "ok"


async def test_a_handle_story_of_a_listed_person_is_read_on_that_igid():
    router, instagram = _handle_router()
    out = await router.read(**_by_handle("story", "example_friend"))

    assert out["status"] == "ok"
    assert instagram.calls == [("instagram", "story", "10000001", 5)]


async def test_a_handle_story_of_a_stranger_is_refused_and_never_requested():
    """The negative: an account that maps to nobody
    is in no room, so its story is `refused` and no story request is made.
    The lookup DID run (it had to, to know whose account this is), and the
    same stranger's PROFILE is read: the empty dispatch log is the reach
    check, not a resolution failure."""
    accounts = FakeInstagramAccountLookup(_HANDLES)
    router, instagram = _handle_router(accounts=accounts)

    out = await router.read(**_by_handle("story", "some.stranger"))
    assert out["status"] == "refused"
    assert "listed" in out["note"]
    assert instagram.calls == []
    assert accounts.asked == ["some.stranger"]

    assert (await router.read(**_by_handle("profile", "some.stranger")))["status"] == "ok"
    assert instagram.calls == [("instagram", "profile", "10000099", 5)]


async def test_a_handle_story_of_a_person_listed_in_no_room_is_refused():
    router, instagram = _handle_router(
        subjects=FakeSocialSubjectIndex({"miles": "10000001"}, out_of_reach=frozenset({"miles"})),
    )
    assert (await router.read(**_by_handle("story", "example_friend")))["status"] == "refused"
    assert instagram.calls == []


async def test_a_handle_to_one_of_two_accounts_reads_that_account_by_name_it_is_ambiguous():
    """A handle NAMES one account, so a friend with two accounts is
    read on the one named, never re-derived through `igids_for` (which would
    answer `ambiguous`). Asked BY NAME the same friend stays `ambiguous`. The
    index's forward lookup is asked only for the by-name read."""
    subjects = FakeSocialSubjectIndex({"robin": frozenset({_ROBIN_A, _ROBIN_B})})
    router, instagram = _handle_router(subjects=subjects)

    assert (await router.read(**_by_handle("story", "robin.work")))["status"] == "ok"
    assert (await router.read(**_by_handle("story", "robin.personal")))["status"] == "ok"
    assert instagram.calls == [
        ("instagram", "story", _ROBIN_B, 5), ("instagram", "story", _ROBIN_A, 5),
    ]
    assert subjects.asked == []

    by_name = await router.read(**{**_STORY, "target": "robin"})
    assert by_name["status"] == "ambiguous"
    assert len(instagram.calls) == 2
    assert subjects.asked == ["robin"]


async def test_a_post_shortcode_reaches_the_backend_untouched_and_resolves_nothing():
    """`instagram/post` is `named`: the shortcode of a pasted link, never
    resolved. Neither the index nor the lookup is consulted."""
    subjects = FakeSocialSubjectIndex({"miles": "10000001"})
    accounts = FakeInstagramAccountLookup(_HANDLES)
    router, instagram = _handle_router(subjects=subjects, accounts=accounts)

    out = await router.read(platform="instagram", what="post", target="SynthReel01", limit=20)
    assert out["status"] == "ok"
    assert instagram.calls == [("instagram", "post", "SynthReel01", 20)]
    assert subjects.asked == [] and accounts.asked == []


@pytest.mark.parametrize("kind", [TARGET_PERSON, TARGET_HANDLE])
async def test_a_post_by_person_or_handle_is_not_served(kind):
    """A post names no person: there is no `person` or `handle` row for it,
    so neither is resolved and nothing is dispatched."""
    router, instagram = _handle_router()
    out = await router.read(
        platform="instagram", what="post", target="miles", limit=5, target_kind=kind,
    )
    assert out["status"] == "unavailable"
    assert instagram.calls == []


async def test_a_handle_story_of_a_listed_person_in_another_room_only_is_refused():
    """The conservative default: a story by handle
    or link must name someone on the ASKING room's roster, the scope a name
    resolves in. Miles is Tier 0, and is not in this room: `refused`, with the
    SAME payload as a stranger's, so the answer does not reveal that he is in
    one of her other rooms. The positive control is the same read with him on
    the roster."""
    router, instagram = _handle_router()

    elsewhere = await router.read(**_by_handle("story", "example_friend", frozenset({"robin"})))
    stranger = await router.read(**_by_handle("story", "some.stranger"))
    assert elsewhere["status"] == "refused"
    assert elsewhere == stranger
    assert instagram.calls == []

    assert (await router.read(**_by_handle("story", "example_friend")))["status"] == "ok"
    assert instagram.calls == [("instagram", "story", "10000001", 5)]


async def test_a_handle_story_with_no_readable_roster_is_unavailable_and_looks_nothing_up():
    """No roster to scope the reach check to is «I couldn't check», never a
    boundary, and the username is not even looked up. A profile needs no
    roster."""
    accounts = FakeInstagramAccountLookup(_HANDLES)
    router, instagram = _handle_router(accounts=accounts)

    out = await router.read(**_by_handle("story", "example_friend", None))
    assert out["status"] == "unavailable"
    assert accounts.asked == [] and instagram.calls == []

    assert (await router.read(**_by_handle("profile", "example_friend", None)))["status"] == "ok"
