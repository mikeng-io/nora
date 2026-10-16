"""`HonchoClient.representation` — durable recall, curated to one question.

Every shape asserted here was read off a LIVE instance, not out of
documentation. That matters more than usual for this endpoint: a client written
against an invented shape passes its own test and is dead against the real one
(`ensure_session` POSTing to a list endpoint, `peer_card` reading a `content`
key Honcho never returns).

The local server below therefore answers like the real one, including for the
requests this code gets WRONG — an unknown peer is 200 with an empty string,
never a 404.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.infra.honcho import HonchoClient
from nora.infra.honcho.client import _conclusions

# The exact body the live instance returned for peer `miles_novak`.
#
# ONE SECTION, and that is the trap. EVERY `miles_novak` document is
# `level='explicit'` (counted in Postgres; that peer is a live deployment and the
# count keeps moving, which is why the claim here is the LEVEL and not the
# number). So this body cannot exercise three of the four shapes
# `format_as_markdown` emits, and a parser could pass against it while handling
# only the one shape it happens to know. Keep it, because it is real; never
# treat it as coverage.
LIVE_BODY = (
    "## Explicit Observations\n\n"
    "[2026-08-05 17:18:04] miles_novak is named Miles\n"
    "[2026-08-05 17:18:04] miles_novak asked who was in the group on August 5, 2026\n"
    "[2026-08-05 17:18:04] miles_novak is from Ridgeline\n"
)

# The exact body the live instance returned for peer `probe_zzz`, for the call
# shape this client sends. Copied verbatim from the wire.
#
# `probe_zzz` is a throwaway peer fed ~48 realistic first-person messages, which
# is what it takes to make Honcho's deriver produce the OTHER levels (explicit,
# deductive and inductive). That is why the fixture is this peer and not
# `miles_novak` — a fixture that cannot produce the real shape is the failure
# CLAUDE.md names.
LIVE_MULTI_LEVEL_BODY = (
    "## Explicit Observations\n"
    "\n"
    "[2026-08-06 10:59:30] probe_zzz does the morning school run; his wife does pickup\n"
    "[2026-08-06 10:59:30] probe_zzz drives to work instead of taking the train\n"
    "\n"
    "## Deductive Observations\n"
    "\n"
    "[2026-08-06 11:04:13] probe_zzz's Thursday unavailability for events is caused by "
    "the late-running team sync that keeps him at work until 9pm\n"
    "   Premises:\n"
    "   - probe_zzz is not coming to events on Thursdays\n"
    "   - On Thursdays team sync runs late and he finishes around 9pm\n"
    "   - Thursday team sync starts at 5 and always overruns; rarely out before 9\n"
    "\n"
    "\n"
    "## Inductive Observations\n"
    "\n"
    " **Pattern** [medium]: probe_zzz maintains a strict bilingual domain separation: "
    "English for work communication, Cantonese for home and personal life\n"
    "   **Type**: behavior\n"
    "   **Sources**:\n"
    "   - speaks Cantonese at home and English at work\n"
    "   - written Chinese is rusty\n"
    "\n"
    " **Pattern** [high]: probe_zzz's spatial and social choices are filtered through "
    "car logistics: flat required parking, social dinners must be walkable\n"
    "   **Type**: behavior\n"
    "   **Sources**:\n"
    "   - building has a parking space, which was non-negotiable when flat hunting\n"
    "   - prefers dinner near Admiralty so he can leave his car in the office garage\n"
    "   - ... and 2 more\n"
    "\n"
    "\n"
)

# `## Contradictions` is the one section of the four that could NOT be taken off
# the wire: `select level, count(*) from documents` over the live database
# returns explicit / deductive / inductive and no contradictions at all, in any
# workspace. So this is built from the emitter itself,
# `src/utils/representation.py::Representation.format_as_markdown`, read out of
# the running container (honcho 3.0.11), and it reproduces that code line for
# line: `f"{id_prefix} **CONTRADICTION**: {obs.content}"`, then
# `"   **Conflicting statements**:"`, then `f"   - {source}"` per source.
#
# Stated rather than omitted, because omitting it is how an explicit-only
# fixture comes to be mistaken for coverage.
CONTRADICTION_BODY = (
    "## Contradictions\n"
    "\n"
    " **CONTRADICTION**: probe_zzz says he never drinks and also describes having a "
    "beer after hikes\n"
    "   **Conflicting statements**:\n"
    "   - does not drink\n"
    "   - maybe a beer after a hike\n"
    "\n"
    "\n"
)


@pytest.fixture
async def server_factory():
    servers: list[TestServer] = []

    async def _make(handler) -> str:
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", handler)
        server = TestServer(app)
        await server.start_server()
        servers.append(server)
        return str(server.make_url("/")).rstrip("/")

    yield _make

    for server in servers:
        await server.close()


def _honcho_like(bodies: dict[str, str]):
    """A server that behaves like Honcho, including for peers it does not know.

    `bodies` maps peer id to its representation. **A peer that is absent gets
    200 and an empty string, not a 404** — that is what the live instance does,
    and a fake that 404'd instead would let a client which cannot tell "no such
    peer" from "nothing known" pass this whole file.
    """

    async def handler(request: web.Request) -> web.Response:
        peer = request.path.rstrip("/").split("/")[-2]
        return web.json_response({"representation": bodies.get(peer, "")})

    return handler


async def test_a_live_shaped_body_becomes_one_conclusion_per_line(server_factory):
    base = await server_factory(_honcho_like({"miles_novak": LIVE_BODY}))
    got = await HonchoClient(base, "nora").representation(
        peer_id="miles_novak", search_query="who is he",
    )
    assert got.reachable is True
    assert got.conclusions == (
        "miles_novak is named Miles",
        "miles_novak asked who was in the group on August 5, 2026",
        "miles_novak is from Ridgeline",
    )


async def test_the_markdown_heading_is_not_a_conclusion(server_factory):
    """`## Explicit Observations` is the store's own filing structure. Returning
    it would hand the model a section title to repeat back to the group."""
    base = await server_factory(_honcho_like({"miles_novak": LIVE_BODY}))
    got = await HonchoClient(base, "nora").representation(
        peer_id="miles_novak", search_query="q",
    )
    assert not any(c.startswith("#") for c in got.conclusions)


async def test_the_timestamp_prefix_is_stripped(server_factory):
    base = await server_factory(_honcho_like({"miles_novak": LIVE_BODY}))
    got = await HonchoClient(base, "nora").representation(
        peer_id="miles_novak", search_query="q",
    )
    assert not any(c.startswith("[") for c in got.conclusions)


async def test_an_unknown_peer_is_reachable_and_empty_never_unreachable(server_factory):
    """THE distinction this return type exists for. Honcho answers 200 with an
    empty string for a peer it has never heard of, so the transport cannot tell
    that from "I know nothing about them" — but it CAN tell both from "the
    request failed", and that is the bit the caller cannot recover afterwards.
    """
    base = await server_factory(_honcho_like({"miles_novak": LIVE_BODY}))
    got = await HonchoClient(base, "nora").representation(
        peer_id="nobody_at_all", search_query="q",
    )
    assert got.reachable is True
    assert got.conclusions == ()


async def test_a_server_error_is_unreachable_not_empty(server_factory):
    async def handler(request: web.Request) -> web.Response:
        return web.json_response({}, status=500)

    base = await server_factory(handler)
    got = await HonchoClient(base, "nora").representation(peer_id="miles_novak", search_query="q")
    assert got.reachable is False


async def test_honcho_being_down_is_unreachable_and_never_raises():
    got = await HonchoClient("http://127.0.0.1:1", "nora").representation(
        peer_id="miles_novak", search_query="q",
    )
    assert got.reachable is False


async def test_a_timeout_is_unreachable_and_bounded(server_factory):
    async def handler(request: web.Request) -> web.Response:
        await asyncio.sleep(0.5)
        return web.json_response({"representation": LIVE_BODY})

    base = await server_factory(handler)
    client = HonchoClient(base, "nora", representation_timeout=0.05)
    got = await client.representation(peer_id="miles_novak", search_query="q")
    assert got.reachable is False


async def test_it_posts_the_search_query_to_the_representation_endpoint(server_factory):
    """The URL, the verb and the body, pinned. `peer_card` was dead because
    nothing checked which endpoint it actually spoke to."""
    seen: list[tuple[str, str, dict]] = []

    async def handler(request: web.Request) -> web.Response:
        seen.append((request.method, request.path, await request.json()))
        return web.json_response({"representation": LIVE_BODY})

    base = await server_factory(handler)
    await HonchoClient(base, "nora").representation(
        peer_id="miles_novak", search_query="邊個係阿豹", max_conclusions=3,
    )
    method, path, body = seen[0]
    assert method == "POST"
    assert path == "/v3/workspaces/nora/peers/miles_novak/representation"
    assert body["search_query"] == "邊個係阿豹"
    assert body["max_conclusions"] == 3


async def test_target_rides_the_body_as_the_observed_peer_and_is_absent_when_omitted(
    server_factory,
):
    """`peer_id` is the OBSERVER, `target` the OBSERVED. Honcho v2 makes Nora a
    peer that joins every session with observe_others=True, so the pair
    derives — and this pins the WIRE: `target` reaches the body when given (so
    `about` really reads Nora's model of a person, not the person's self-
    model), and is absent when omitted (self-model). Reverting the client's
    `if target is not None` turns this red."""
    seen: list[dict] = []

    async def handler(request: web.Request) -> web.Response:
        seen.append(await request.json())
        return web.json_response({"representation": ""})

    base = await server_factory(handler)
    client = HonchoClient(base, "nora")
    await client.representation(peer_id="nora", search_query="q", target="miles_novak")
    assert seen[0]["target"] == "miles_novak"
    await client.representation(peer_id="miles_novak", search_query="q")
    assert "target" not in seen[1]


async def test_a_body_of_the_wrong_shape_is_reachable_with_nothing_in_it(server_factory):
    """An upstream shape change must not read as an outage: the request
    demonstrably succeeded, and saying otherwise would send an operator after
    the wrong service."""
    async def handler(request: web.Request) -> web.Response:
        return web.json_response({"representation": None})

    base = await server_factory(handler)
    got = await HonchoClient(base, "nora").representation(peer_id="miles_novak", search_query="q")
    assert got.reachable is True
    assert got.conclusions == ()


# ---- the four levels -------------------------------------------------------
#
# Everything below runs the parser over output the live instance actually
# produced (or, for contradictions, over the emitter's own source). A parser
# that is not level-aware reads every premise and every source bullet as an
# independent fact, and `.lstrip("-*")` eats the markdown bold so patterns read
# `Pattern** [high]: …`.


async def test_the_three_derived_levels_all_become_plain_conclusions(server_factory):
    """One conclusion per OBSERVATION — not per line. A deductive observation is
    a conclusion plus its premises, and an inductive one is a pattern plus its
    type and sources; only the conclusion is a thing Nora knows about a person.
    """
    base = await server_factory(_honcho_like({"probe_zzz": LIVE_MULTI_LEVEL_BODY}))
    got = await HonchoClient(base, "nora").representation(
        peer_id="probe_zzz", search_query="how does he get to work",
    )
    assert got.conclusions == (
        "probe_zzz does the morning school run; his wife does pickup",
        "probe_zzz drives to work instead of taking the train",
        "probe_zzz's Thursday unavailability for events is caused by the late-running "
        "team sync that keeps him at work until 9pm",
        "probe_zzz maintains a strict bilingual domain separation: English for work "
        "communication, Cantonese for home and personal life",
        "probe_zzz's spatial and social choices are filtered through car logistics: "
        "flat required parking, social dinners must be walkable",
    )


async def test_a_premise_never_returns_as_an_independent_fact(server_factory):
    """The premises under a deduction are the explicit observations it was built
    FROM. Flattened into the list they read as separate things Nora remembers,
    which both inflates the count and hands the model the same fact twice."""
    base = await server_factory(_honcho_like({"probe_zzz": LIVE_MULTI_LEVEL_BODY}))
    got = await HonchoClient(base, "nora").representation(
        peer_id="probe_zzz", search_query="q",
    )
    assert "probe_zzz is not coming to events on Thursdays" not in got.conclusions
    assert "written Chinese is rusty" not in got.conclusions
    assert not any(c.startswith("... and ") for c in got.conclusions)
    assert not any(c.endswith(":") for c in got.conclusions)


async def test_no_conclusion_carries_markdown_the_old_parser_would_have_mangled(
        server_factory):
    """`.lstrip("-*")` strips the asterisks off ` **Pattern** [high]: …` from the
    LEFT only, so the line arrived as `Pattern** [high]: …` — a section label,
    a confidence token and an orphaned `**`, presented to the model as a fact."""
    base = await server_factory(_honcho_like({"probe_zzz": LIVE_MULTI_LEVEL_BODY}))
    got = await HonchoClient(base, "nora").representation(
        peer_id="probe_zzz", search_query="q",
    )
    assert got.conclusions
    for c in got.conclusions:
        assert "**" not in c
        assert not c.startswith(("Pattern", "Type", "Sources", "Premises"))


async def test_a_contradiction_is_its_description_and_not_its_label(server_factory):
    """The fourth section. Its `content` already reads as a sentence about the
    person, so the `**CONTRADICTION**:` scaffolding is dropped like every other
    label; the conflicting statements underneath are sources, not facts."""
    base = await server_factory(_honcho_like({"probe_zzz": CONTRADICTION_BODY}))
    got = await HonchoClient(base, "nora").representation(
        peer_id="probe_zzz", search_query="q",
    )
    assert got.conclusions == (
        "probe_zzz says he never drinks and also describes having a beer after hikes",
    )


async def test_an_unknown_future_section_contributes_nothing_rather_than_garbage(
        server_factory):
    """Honcho's formatter is versioned with Honcho, not with Nora. A level added
    upstream must degrade to silence — the alternative is a new label shape read
    out to the group as something Nora believes.
    """
    body = (
        "## Abductive Observations\n\n"
        " **Hypothesis** [low]: probe_zzz may own a boat\n"
        "   **Basis**:\n"
        "   - mentioned a marina\n"
    )
    base = await server_factory(_honcho_like({"probe_zzz": body}))
    got = await HonchoClient(base, "nora").representation(
        peer_id="probe_zzz", search_query="q",
    )
    assert got.reachable is True
    assert got.conclusions == ()


# ---- the call shape --------------------------------------------------------
#
# The fake below is not a stub with canned bodies: it REIMPLEMENTS the retrieval
# arithmetic in `src/crud/representation.py::
# _get_working_representation_internal` and the emitter in
# `src/utils/representation.py::Representation.format_as_markdown`, both read out
# of the running container (honcho 3.0.11).
#
# It has to. A fake that returns the same rows whatever it is asked cannot tell
# `search_top_k=9` from `search_top_k=3`, and the property this section pins is
# *entirely* a matter of which numbers were sent — the endpoint answers 200 with
# a perfectly well-formed body whatever they are.


@dataclass(frozen=True)
class _Doc:
    """One row of `documents`, with the three columns the three channels rank on."""

    level: str
    content: str
    distance: float      # cosine distance to the ONE query these tests ask
    times_derived: int   # `most_derived` orders by this DESC
    created_at: str      # `recent` orders by this DESC


# `distance` is stated per-document rather than computed, because embedding a
# sentence is not what is under test here — WHICH CHANNEL REACHES A ROW is.
#
# Three rows are reachable through exactly one channel each, and that is the
# whole design of this corpus:
#   * SEMANTIC_ONLY_*  — near the query, old, derived once.
#   * DERIVED_ONLY     — far from the query, oldest, derived nine times.
#   * RECENT_ONLY      — far from the query, newest, derived once.
# The eight fillers sit between, and they are what a semantic-only call returns
# INSTEAD of the other two.
DERIVED_ONLY = "probe_zzz refuses to get an automatic car"
RECENT_ONLY = "probe_zzz is on annual leave in December"
SEMANTIC_ONLY = "probe_zzz drives to work instead of taking the train"

_CORPUS = (
    _Doc("explicit", SEMANTIC_ONLY, 0.10, 1, "2026-08-06T10:00"),
    _Doc("explicit", "probe_zzz does the morning school run", 0.20, 1, "2026-08-06T10:01"),
    _Doc("explicit", "probe_zzz's office is in Admiralty", 0.35, 1, "2026-08-06T10:02"),
    _Doc("explicit", "probe_zzz lives in Tai Chi", 0.62, 1, "2026-08-06T10:03"),
    _Doc("explicit", "probe_zzz has a four-year-old daughter", 0.64, 1, "2026-08-06T10:04"),
    _Doc("explicit", "probe_zzz plays badminton on Tuesdays", 0.66, 1, "2026-08-06T10:05"),
    _Doc("explicit", "probe_zzz does not fly", 0.68, 1, "2026-08-06T10:06"),
    _Doc("deductive", "probe_zzz graduated from HKUST in 2014", 0.70, 1, "2026-08-06T10:07"),
    _Doc("explicit", "probe_zzz's wife is called Ada", 0.72, 1, "2026-08-06T10:08"),
    _Doc("explicit", "probe_zzz keeps his phone on silent", 0.74, 1, "2026-08-06T10:09"),
    _Doc("inductive", "probe_zzz prefers messages to calls", 0.76, 1, "2026-08-06T10:10"),
    _Doc("explicit", DERIVED_ONLY, 0.95, 9, "2026-08-06T09:00"),
    _Doc("explicit", RECENT_ONLY, 0.97, 1, "2026-08-06T13:00"),
)


def _format_as_markdown(docs: list[_Doc]) -> str:
    """`Representation.format_as_markdown`, reproduced. Sections in the emitter's
    order, `[stamp]` on explicit and deductive, `**Pattern** [conf]:` on
    inductive, and the indented sub-lines that the parser has to discard."""
    parts: list[str] = []
    for level, heading in (
        ("explicit", "## Explicit Observations"),
        ("deductive", "## Deductive Observations"),
        ("inductive", "## Inductive Observations"),
    ):
        rows = [d for d in docs if d.level == level]
        if not rows:
            continue
        parts.append(heading + "\n")
        for d in rows:
            if level == "inductive":
                parts.append(f" **Pattern** [high]: {d.content}")
                parts.append("   **Type**: behavior")
                parts.append("   **Sources**:")
                parts.append("   - a source line that is not a conclusion")
            else:
                parts.append(f"[{d.created_at}] {d.content}")
                if level == "deductive":
                    parts.append("   Premises:")
                    parts.append("   - a premise line that is not a conclusion")
            parts.append("")
        parts.append("")
    return "\n".join(parts)


def _representation_engine(corpus=_CORPUS, seen: list | None = None):
    """A server that spends `max_conclusions` the way the real one does.

Reproduces `_get_working_representation_internal` exactly, including the
three details that make a mis-sized call shape a no-op rather than a mistake of
degree: `search_top_k` REPLACES `total // 3` when supplied, the derived
    channel is skipped entirely unless `include_most_frequent` is true, and the
    recent budget is whatever the other two left — so `search_top_k == total`
    leaves nothing for either.

    The budgets are computed from the REQUESTED counts, never from the returned
    ones, so a `search_max_distance` that rejects rows shrinks the answer rather
    than being made up elsewhere. That is upstream's behaviour, and a fake that
    quietly topped the budget back up would hide it.
    """

    async def handler(request: web.Request) -> web.Response:
        body = await request.json()
        if seen is not None:
            seen.append(body)
        total = body.get("max_conclusions", 25)
        query = body.get("search_query")
        top_k = body.get("search_top_k")
        most = bool(body.get("include_most_frequent") or False)
        max_distance = body.get("search_max_distance")

        n_semantic = (
            min(max(0, top_k if top_k is not None else total // 3), total) if query else 0
        )
        if query and most:
            n_derived = min(max(0, total // 3), total - n_semantic)
        elif most:
            n_derived = min(max(0, total // 2), total - n_semantic)
        else:
            n_derived = 0
        n_recent = total - n_semantic - n_derived

        picked: list[_Doc] = []
        if query:
            ranked = sorted(corpus, key=lambda d: d.distance)
            if max_distance is not None:
                ranked = [d for d in ranked if d.distance <= max_distance]
            picked += ranked[:n_semantic]
        if most:
            picked += sorted(
                corpus, key=lambda d: (-d.times_derived, d.created_at), reverse=False,
            )[:n_derived]
        picked += sorted(corpus, key=lambda d: d.created_at, reverse=True)[:n_recent]

        # `merge_representation` dedupes and then sorts each level by created_at.
        unique = {d.content: d for d in picked}
        rows = sorted(unique.values(), key=lambda d: d.created_at)
        return web.json_response({"representation": _format_as_markdown(rows)})

    return handler


async def test_all_three_channels_reach_the_answer(server_factory):
    """THE probe. Two of the three rows below are unreachable by semantic search
    at any `top_k`, because they are the two furthest from the query — and one
    of them is the single most-derived conclusion in the corpus, which is
    exactly the durable trait ADR 0001 says Honcho exists to hold.

    Revert `search_top_k` to `max_conclusions`, or drop `include_most_frequent`,
    and both disappear while this call still answers 200 with a well-formed
    body — which is precisely why that shape is invisible.
    """
    base = await server_factory(_representation_engine())
    got = await HonchoClient(base, "nora").representation(
        peer_id="probe_zzz", search_query="how does he get to work",
    )
    assert got.reachable is True
    assert SEMANTIC_ONLY in got.conclusions
    assert DERIVED_ONLY in got.conclusions
    assert RECENT_ONLY in got.conclusions


async def test_the_old_shape_could_not_have_reached_two_of_them(server_factory):
    """The counterfactual, run against the SAME engine, so the test above is
    measuring the call shape rather than the fake's generosity.

    This is the call shape the client must not send: `search_top_k ==
    max_conclusions`, no `include_most_frequent`, no distance floor. It
    computes to semantic 5, derived 0, recent 0.
    """
    seen: list[dict] = []
    base = await server_factory(_representation_engine(seen=seen))
    async with aiohttp.ClientSession() as session:
        url = f"{base}/v3/workspaces/nora/peers/probe_zzz/representation"
        async with session.post(url, json={
            "search_query": "how does he get to work",
            "max_conclusions": 5,
            "search_top_k": 5,
        }) as r:
            old = _conclusions((await r.json())["representation"])

    assert SEMANTIC_ONLY in old            # the semantic channel works
    assert DERIVED_ONLY not in old         # …and is the only one that does
    assert RECENT_ONLY not in old


async def test_a_question_the_corpus_cannot_answer_returns_no_nearest_neighbours(
        server_factory):
    """pgvector ranks, it does not judge: without a distance floor the semantic
    channel returns `top_k` rows for a question about nothing in the corpus, and
    Nora reads the nearest unrelated facts out as though they were an answer.
    At d=0.8, «quantum chromodynamics» comes back with the VW Golf.

    The other two channels still answer, which is the point of blending them:
    "I could not find anything about THAT" is not the same as "I know nothing".
    """
    far = tuple(d for d in _CORPUS if d.distance > 0.5)
    base = await server_factory(_representation_engine(corpus=far))
    got = await HonchoClient(base, "nora").representation(
        peer_id="probe_zzz", search_query="a question about nothing in here",
    )
    assert got.reachable is True
    assert DERIVED_ONLY in got.conclusions
    assert RECENT_ONLY in got.conclusions


async def test_the_budget_is_split_three_ways_and_never_spent_on_one_channel(
        server_factory):
    """The numbers, pinned on this side. `search_top_k` strictly below
    `max_conclusions` is what leaves anything for the other two — upstream
    defaults it to `total // 3`, and it is sent explicitly so a change to that
    default arrives as a failing test rather than as a quieter Nora."""
    seen: list[dict] = []
    base = await server_factory(_representation_engine(seen=seen))
    await HonchoClient(base, "nora").representation(
        peer_id="probe_zzz", search_query="q", max_conclusions=9,
    )
    body = seen[0]
    assert body["max_conclusions"] == 9
    assert body["search_top_k"] == 3
    assert body["search_top_k"] < body["max_conclusions"]
    assert body["include_most_frequent"] is True
    assert body["search_max_distance"] == 0.5


async def test_search_top_k_stays_inside_the_apis_lower_bound(server_factory):
    """`ge=1` on the endpoint. `max_conclusions // 3` is 0 for any budget under
    3, and a 0 would be a 422 — i.e. `Recollection(reachable=False)`, an outage
    reported for a number this client chose itself."""
    seen: list[dict] = []
    base = await server_factory(_representation_engine(seen=seen))
    await HonchoClient(base, "nora").representation(
        peer_id="probe_zzz", search_query="q", max_conclusions=2,
    )
    assert seen[0]["search_top_k"] == 1
