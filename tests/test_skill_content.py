"""Every shipped skill — that it loads, and that it stays a skill.

These guard the discipline design/17 asks of a skill: it must be about a
DOMAIN, not about any one scenario (a skill that names a scenario is a scenario
that fails its neighbour), it must say when to load AND when not to, and it must
stay small — most of what looks like skill content belongs a layer up (doctrine)
or down (tools).

The second half of this file (2026-09-13) is a different kind of guard for one
skill: `self-description` makes FACTUAL claims about things outside itself, and
those are pinned to the reads that make them true rather than to a list of words
they must avoid.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from nora.skills.loader import SkillStore

SKILLS_DIR = Path(__file__).resolve().parents[1] / "nora" / "skills"

# Per-skill scenario lists: the concrete trips/asks a skill must NOT name,
# because naming one is the failure mode (design/17, operator-insisted).
_SCENARIOS: dict[str, tuple[str, ...]] = {
    "navigation": ("dinner", "airport", "shopping", "晚飯", "機場", "食飯", "買嘢"),
    "personalization": ("game", "restaurant", "film", "game night", "食飯", "睇戲"),
    # The scenarios that produced #227: a bare hostname and a question about a
    # website. A self-account that answers by naming pages, a version number or
    # a repository is the same failure one layer in — it describes an instance
    # instead of the shape, and the instance is what goes stale in silence.
    "self-description": ("website", "網站", "homepage", "version number",
                         "release notes", "github"),
}


@pytest.fixture(scope="module")
def store() -> SkillStore:
    return SkillStore(SKILLS_DIR)


@pytest.fixture
def skills(store: SkillStore) -> tuple:
    return store.available()


def test_every_shipped_skill_loads_and_is_well_formed(skills):
    assert skills, "at least one skill must ship in nora/skills/"
    for skill in skills:
        assert skill.name.strip()
        assert skill.description.strip()
        assert skill.instructions.strip()


def test_every_description_says_both_when_to_load_and_when_not_to(skills):
    """A skill costs a turn to activate (design/12 §4), so it must earn it — a
    bare live check does NOT need it, a plan does. The description carries both:
    a when-to-load trigger and a negative half naming what it is NOT for."""
    for skill in skills:
        desc = skill.description
        assert len(desc) > 40, f"{skill.name}: description too thin to steer"
        # The negative half — the load-trigger names something it is NOT for.
        assert "Not for" in desc or "not for" in desc, (
            f"{skill.name}: description must say when NOT to load")


def test_every_skill_names_no_scenario(skills):
    """design/17 (operator-insisted): the skill handles the dinner run, the
    airport run and the shopping trip by being about wayfinding, not about any
    one of them. Naming a scenario is the failure mode."""
    for skill in skills:
        haystack = (skill.description + "\n" + skill.instructions).lower()
        for scenario in _SCENARIOS.get(skill.name, ()):
            assert scenario not in haystack, (
                f"{skill.name} names a scenario: {scenario!r}")


def test_every_skill_stays_small(skills):
    """Most of what looks like skill content belongs a layer up or down; what is
    left is genuinely small. A large skill is stealing a lower layer's job."""
    for skill in skills:
        lines = [ln for ln in skill.instructions.splitlines() if ln.strip()]
        assert len(lines) <= 30, (
            f"{skill.name} skill body has {len(lines)} non-blank lines")


# --- The self-account's factual claims, and the reads that make them true -------
#
# 2026-09-13, after review. What guarded this file was `_SCENARIOS` alone — a
# substring DENYLIST — and a denylist cannot guard a file whose failure mode is a
# SENTENCE. Measured on the reviewed tree: deleting the whole web-view sentence
# passed, and rewriting it to name two of the portal's own pages under a synonym
# the list missed ("portal") passed too. Nothing pinned a single factual claim.
#
# So each claim is pinned by BOTH halves. The sentence has to still be in the
# body, and the property it asserts has to still hold when something re-reads it
# — the portal payload builders, the live tool registry, her actual prompt
# inputs, the shipped config. Deleting or rewording the sentence reddens the
# first half; the code changing under it reddens the second. That is the
# difference between a claim a test HOLDS and a word a test happens to forbid.
#
# **Changing one of these phrases is changing a claim about the world.** Edit the
# phrase here in the same commit, and say in the message what re-read proves the
# new wording — that is the cost, and it is the point.

ROOT = Path(__file__).resolve().parents[1]
PORTAL_ROUTES_DIR = ROOT / "app" / "src" / "app" / "[locale]" / "(portal)"


def _flat(text: str) -> str:
    """Whitespace-insensitive, so a reflow of the markdown is not a claim change."""
    return " ".join(text.split())


@pytest.fixture(scope="module")
def self_account(store: SkillStore) -> str:
    for skill in store.available():
        if skill.name == "self-description":
            return skill.instructions
    raise AssertionError("the self-description skill must ship")


def _portal_routes() -> tuple[str, ...]:
    """The portal's ACTUAL top-level routes, read off the tree rather than listed
    here — the same derive-don't-hand-copy rule the metric label vocabularies are
    held to. A hand-written list is exactly the stale list this file exists to
    keep out of the skill body."""
    return tuple(sorted(p.name for p in PORTAL_ROUTES_DIR.iterdir() if p.is_dir()))


def _her_own_inputs() -> tuple[tuple[str, str], ...]:
    """Everything she actually reads about herself: the doctrine the tag path
    assembles, plus every shipped skill. This is the source for the NEGATIVE
    claim — «its address and what it has on it were never given to you» is a
    statement about these bodies, so it is measured against them."""
    from nora.doctrine.loader import DoctrineStore
    from nora.obs.metrics import Metrics

    doctrine = DoctrineStore(
        ROOT / "nora" / "doctrine", canary="C", metrics=Metrics(),
    ).get(applies_to="tag")
    inputs = [(f"doctrine:{s.name}", s.body) for s in doctrine.sections]
    inputs += [
        (f"skill:{s.name}", s.description + "\n" + s.instructions)
        for s in SkillStore(SKILLS_DIR).available()
    ]
    return tuple(inputs)


def test_the_self_account_names_no_portal_route_as_a_page(self_account):
    """#227's failure was a LIST of pages improvised into existence, so the fix
    must not ship a list of pages either — a route list in a file that is baked
    into an image goes stale the first time the portal gains or loses one, and
    silently.

    Derived from the portal's own directory, not written out here, so a new route
    is covered the day it lands."""
    routes = _portal_routes()
    assert len(routes) >= 2, "the portal's route directory must be readable here"
    body = self_account.lower()
    for route in routes:
        for noun in ("page", "tab", "section", "view", "endpoint"):
            assert f"{route} {noun}" not in body, f"names a route as a {noun}: {route}"


def test_the_self_account_carries_no_address(self_account):
    """The one thing she genuinely was not given. A URL or a bare hostname in this
    body would be #227's guess written down as doctrine — right today, wrong the
    first time anything moves, and unfalsifiable from inside a room."""
    assert not re.search(r"https?://", self_account)
    assert "/app/v1/" not in self_account
    assert not re.search(
        r"\b[a-z0-9][a-z0-9-]*\.(?:com|net|org|io|dev|app|test|xyz|hk)\b",
        self_account.lower(),
    )


def test_nothing_she_reads_names_the_portal_or_its_address(self_account):
    """The GROUNDING for the body's negative claim, measured against her real
    inputs rather than asserted.

    «its address and what it has on it were never given to you» is a statement
    about what is in her prompt, and it is true only while nothing in the doctrine
    or the skills names a route or an address. The day one of them does, the
    sentence becomes the false kind of negative — the one this file's review
    found — and this is what says so instead of the room finding out."""
    assert _flat("its address and what it has on it were never given to you") in _flat(
        self_account)
    routes = _portal_routes()
    for where, body in _her_own_inputs():
        assert "http://" not in body and "https://" not in body, where
        assert "/app/v1/" not in body, where
        low = body.lower()
        for route in routes:
            for noun in ("page", "tab", "section"):
                assert f"{route} {noun}" not in low, f"{where} names {route} {noun}"


def test_the_self_account_says_the_portal_shows_her_own_instructions_and_tools(
        self_account):
    """B1: the reviewed version said «you do not hold its pages or their contents»,
    and the code says otherwise about the exact two pages that produced #227.

    The grounding is executed, not described: the portal's doctrine payload is
    built from the SAME `DoctrineStore.get(applies_to="tag")` call the tag path
    makes, so its bodies are the text in her prompt every turn; the tools payload
    IS the deployed agent's own tool surface; and the skills payload carries this
    very file's instructions. Describing that part is reading, not guessing — and
    a denial would have been the inverse of #227's bug shipped as its correction.
    """
    import asyncio

    from nora.agent.registry import ToolRegistry
    from nora.doctrine.loader import DoctrineStore
    from nora.obs.metrics import Metrics
    from nora.web.doctrine import build_doctrine_payload
    from nora.web.skills import build_skills_payload
    from nora.web.tools import build_tools_payload

    assert _flat(
        "Part of what it shows is yours — your own instructions, and what you can "
        "do — so describing that part is reading rather than guessing."
    ) in _flat(self_account)

    store = DoctrineStore(ROOT / "nora" / "doctrine", canary="C", metrics=Metrics())
    served = build_doctrine_payload(doctrine_store=store)
    in_her_prompt = {s.body.strip() for s in store.get(applies_to="tag").sections}
    assert served and all(s["body"] for s in served)
    assert {s["body"] for s in served} == in_her_prompt

    assert {t["name"] for t in build_tools_payload()} == set(ToolRegistry().tool_names())

    skills = asyncio.run(build_skills_payload(skills_dir=SKILLS_DIR))
    assert self_account in {s["instructions"] for s in skills}


def test_the_self_account_says_who_signs_into_the_web_view(self_account):
    """B2: «your operator also runs a private, login-gated web view» was wrong
    about who it is for. The gate admits anyone the roster resolves — she would
    have said "my operator's" to a room whose members hold their own logins.

    HONEST SCOPE: a structural read of the gate's source, not an execution of it
    (the gate is TypeScript). What it pins is that admission is decided by roster
    membership — every person, any of their own account blocks — and NOT by a
    hard-coded identity. No count is asserted anywhere: a count of real people is
    PII and it rots."""
    assert _flat(
        "the people you talk to sign into it with their own accounts, not your "
        "operator alone"
    ) in _flat(self_account)

    gate = (ROOT / "app" / "src" / "lib" / "require-session.ts").read_text()
    allowlist = (ROOT / "app" / "src" / "lib" / "allowlist.ts").read_text()
    assert "resolveDiscordPerson" in gate
    assert "IDENTITY_TOML_PATH" in gate
    # EVERY person, not one: the resolver walks the whole roster and matches any
    # of that person's own account blocks.
    assert "Object.entries(persons)" in allowlist
    assert "discord" in allowlist
    # Admission compares the CALLER's own id against that person's own blocks —
    # not against a constant. A hard-coded identity here is what would make the
    # sentence false, and `tests/architecture/test_no_real_identifiers_in_the_
    # tree.py` is what keeps the id in this file's doc comment a placeholder.
    assert "block.user_id === discordUserId" in allowlist


def test_the_self_account_says_the_build_is_asked_not_inferred(self_account):
    """The reviewed version said «You cannot read your own build», and that is
    false wherever the image carries a stamp: `version` is a registered tool, it
    reads the stamp baked in at build time, and it answers `ok` with a name.

    What is true on EVERY deployment is the shape — there may be a stamp or there
    may be none, the tool says which, and neither answer is ever worked out from
    the room. Both outcomes are pinned off the LIVE catalogue, so a tool that
    stopped carrying one of them reddens this rather than leaving the sentence
    standing over nothing."""
    from nora.agent.registry import ToolRegistry

    assert _flat(
        "Your build may carry a stamp or carry none; which of those is true here "
        "is something you ask, never something you work out."
    ) in _flat(self_account)

    catalog = {t["name"]: t for t in ToolRegistry().catalog()}
    assert "version" in catalog, "she must have a way to ask"
    described = catalog["version"]["description"]
    assert "ok" in described and "unavailable" in described
    assert catalog["version"]["source_tier"] == "confirmed"


def test_the_self_account_does_not_assert_a_deployment_it_cannot_read(
        self_account, example_config_path):
    """W2: the ambient half and what carries between turns are BOTH config-gated,
    this file is baked into the image (`COPY nora/` — `nora/skills` is not a
    bind-mount the way `nora/doctrine` is), and the flags live in a
    per-deployment `config.toml` the file does not travel with.

    Grounded on the SHIPPED posture rather than on one live machine: under
    `config.example.toml` every one of those flags is off, so an unconditional
    sentence is false on a fresh deployment and nothing reports it. That is
    `identity.md`'s eight-week rot one layer out."""
    import tomllib

    assert _flat(
        "Whether a deployment runs that, or the half above, is a setting you "
        "cannot read"
    ) in _flat(self_account)

    shipped = tomllib.loads(example_config_path.read_text())
    # If one of these ever ships ON the caveat is still true (it says "whether"),
    # but the reason it was written stops being visible — so the read is pinned.
    assert shipped["participation"]["enabled"] is False
    assert shipped["notes"]["enabled"] is False
    assert shipped["standing"]["enabled"] is False

    compose = (ROOT / "docker-compose.yml").read_text()
    assert "./nora/doctrine:/app/nora/doctrine:ro" in compose
    assert "./nora/skills" not in compose, (
        "a skills bind-mount would change the deploy story this test is about")
    assert "COPY nora/ ./nora/" in (ROOT / "Dockerfile").read_text()
