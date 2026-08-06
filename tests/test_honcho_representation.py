"""`HonchoClient.representation` — durable recall, curated to one question.

Every shape asserted here was read off the LIVE instance on 2026-08-06, not out
of documentation. That matters more than usual for this endpoint: its two
sibling reads shipped dead against invented shapes (`ensure_session` POSTing to
a list endpoint, `peer_card` reading a `content` key Honcho has never returned),
and both had a passing test at the time.

The local server below therefore answers like the real one, including for the
requests this code gets WRONG — an unknown peer is 200 with an empty string,
never a 404.
"""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.infra.honcho import HonchoClient

# The exact body the live instance returned for peer `miles_novak` on 2026-08-06.
#
# ONE SECTION, and that is the trap this file used to be. `miles_novak` has 16
# documents and every one of them is `level='explicit'` (counted in Postgres),
# so this body cannot exercise three of the four shapes `format_as_markdown`
# emits — and for months the parser was "passing" against the only shape it
# happened to handle. Keep it, because it is real; never treat it as coverage.
LIVE_BODY = (
    "## Explicit Observations\n\n"
    "[2026-08-05 17:18:04] miles_novak is named Miles\n"
    "[2026-08-05 17:18:04] miles_novak asked who was in the group on August 5, 2026\n"
    "[2026-08-05 17:18:04] miles_novak is from Ridgeline\n"
)

# The exact body the live instance returned for peer `probe_zzz` on 2026-08-06,
# for the call shape this client now sends. Copied verbatim from the wire.
#
# `probe_zzz` is a throwaway peer fed ~48 realistic first-person messages, which
# is what it takes to make Honcho's deriver produce the OTHER levels: 77
# explicit, 14 deductive, 11 inductive. That is why the fixture is this peer and
# not `miles_novak` — a fixture that cannot produce the real shape is the failure
# CLAUDE.md names, and this subsystem has now shipped three of them.
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
# the wire: `select level, count(*) from documents` over the whole live database
# returns explicit / deductive / inductive and no contradictions at all, in any
# workspace — nothing has ever produced one. So this is built from the emitter
# itself, `src/utils/representation.py::Representation.format_as_markdown`, read
# out of the running container (honcho 3.0.11), and it reproduces that code
# line for line: `f"{id_prefix} **CONTRADICTION**: {obs.content}"`, then
# `"   **Conflicting statements**:"`, then `f"   - {source}"` per source.
#
# Stated rather than omitted, because omitting it is how the explicit-only
# fixture above came to be mistaken for coverage.
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


async def test_it_never_sends_a_target(server_factory):
    """`target=` is theory-of-mind and 404s unless the target peer exists —
    Nora is not a peer, so passing one would turn every recall into a failure
    (design/11 §1.3)."""
    seen: list[dict] = []

    async def handler(request: web.Request) -> web.Response:
        seen.append(await request.json())
        return web.json_response({"representation": ""})

    base = await server_factory(handler)
    await HonchoClient(base, "nora").representation(peer_id="miles_novak", search_query="q")
    assert "target" not in seen[0]


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
# produced (or, for contradictions, over the emitter's own source). Before this
# commit the same body produced 151 "conclusions" from 25 documents: every
# premise and every source bullet arrived as an independent fact, and
# `.lstrip("-*")` ate the markdown bold so patterns read `Pattern** [high]: …`.


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
    upstream must degrade to silence — the alternative is what this commit
    fixes, a new label shape read out to the group as something Nora believes.
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
