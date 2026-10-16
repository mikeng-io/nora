"""`SocialBridge` — the host-side read-only boundary (DEC-151, R-86's second
layer). The OpenCLI subprocess spawn is injected as a constructor collaborator
(test-seam policy), so every test drives the REAL handler through a real
aiohttp server with a fake runner that behaves like `opencli` — including its
failure shapes: non-zero exits, non-JSON stdout on exit 0, a timeout, and
stderr that must never reach the response body.

The property that matters most: a request whose (platform, what) is outside
`READ_ONLY_ALLOWLIST` — every write-ish verb — is refused with a 400 and the
runner is NEVER invoked. Revert the allowlist check and those tests go red.
"""

from __future__ import annotations

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer

from nora.infra.social.bridge_server import READ_ONLY_ALLOWLIST, SocialBridge

BIN = "opencli-test"


class _FakeRunner:
    """The `opencli` subprocess, standing in for the real spawn: records every
    argv it is handed (the probe the refusal tests need) and answers with the
    scripted (returncode, stdout, stderr)."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.returncode: int | None = 0
        self.stdout = '{"ok": true}'
        self.stderr = ""

    async def __call__(
        self, argv: list[str], timeout_seconds: float,
    ) -> tuple[int | None, str, str]:
        self.calls.append(argv)
        return self.returncode, self.stdout, self.stderr


@pytest.fixture
async def bridge():
    runner = _FakeRunner()
    server = TestServer(SocialBridge(opencli_bin=BIN, runner=runner).app())
    await server.start_server()
    session = ClientSession()
    yield runner, session, str(server.make_url("")).rstrip("/")
    await session.close()
    await server.close()


async def _read(session: ClientSession, base: str, body: object) -> tuple[int, dict]:
    async with session.post(f"{base}/read", json=body) as response:
        return response.status, await response.json()


async def test_health(bridge):
    _runner, session, base = bridge
    async with session.get(f"{base}/health") as response:
        assert response.status == 200
        assert await response.json() == {"ok": True}


# ---- the read-only boundary ------------------------------------------------


@pytest.mark.parametrize(
    "what",
    ["like", "unlike", "follow", "unfollow", "delete", "comment",
     "retweet", "unretweet", "block", "unblock", "reply", "quote", "save",
     "login", "story", "note"],
)
async def test_every_write_ish_command_is_refused_and_never_spawns(bridge, what):
    """The boundary itself: a write verb can never be constructed, because the
    (platform, what) vocabulary names READS only — every ACTUAL write verb is
    outside the allowlist."""
    runner, session, base = bridge
    for platform in ("x", "instagram"):
        status, body = await _read(
            session, base,
            {"platform": platform, "what": what, "target": "nasa", "limit": 5},
        )
        assert status == 400
        assert body["status"] == "unavailable"
    assert runner.calls == []


async def test_instagram_post_is_refused_and_never_spawns(bridge):
    """Host-side: OpenCLI has no caption-by-URL command and serves no Instagram
    read at all (`x_read` and `x_search` are X only). A
    request for one could only have been constructed around the tool, so it
    is refused here."""
    runner, session, base = bridge
    status, body = await _read(
        session, base,
        {"platform": "instagram", "what": "post",
         "target": "https://www.instagram.com/p/abc/", "limit": 5},
    )
    assert status == 400
    assert body["status"] == "unavailable"
    assert runner.calls == []


@pytest.mark.parametrize("what", ["profile", "posts", "story", "search"])
async def test_instagram_reads_moved_to_the_bridge_are_refused_and_never_spawn(
    bridge, what,
):
    """Host-side, per verb. Instagram profile and story are `instagram_read`'s and account
    search is `instagram_search`'s, all over the instagram-bridge, and
    `posts` has no mechanism anywhere, so OpenCLI serves none of them. A
    request for one could only have been built around the tools, and it is
    refused here without a subprocess."""
    runner, session, base = bridge
    status, body = await _read(
        session, base,
        {"platform": "instagram", "what": what, "target": "nasa", "limit": 5},
    )
    assert status == 400
    assert body["status"] == "unavailable"
    assert runner.calls == []


async def test_an_unknown_platform_is_refused_without_spawning(bridge):
    runner, session, base = bridge
    status, _body = await _read(
        session, base, {"platform": "threads", "what": "post", "target": "1", "limit": 5},
    )
    assert status == 400
    assert runner.calls == []


async def test_a_non_json_body_is_refused_without_spawning(bridge):
    runner, session, base = bridge
    async with session.post(
        f"{base}/read", data="not json", headers={"Content-Type": "text/plain"},
    ) as response:
        assert response.status == 400
    assert runner.calls == []


# ---- the allowed combos build exactly one argv, no shell --------------------

# (platform, what, target, expected argv). Module-level so the coverage guard
# below can DERIVE the covered pairs from it rather than hand-copying them.
_ARGV_CASES = [
    ("x", "post", "https://x.com/nasa/status/1234567890?s=20",
     [BIN, "--profile", "nora", "twitter", "thread", "1234567890", "-f", "json"]),
    ("x", "post", "1234567890",
     [BIN, "--profile", "nora", "twitter", "thread", "1234567890", "-f", "json"]),
    ("x", "profile", "nasa",
     [BIN, "--profile", "nora", "twitter", "profile", "nasa", "-f", "json"]),
    ("x", "posts", "nasa",
     [BIN, "--profile", "nora", "twitter", "tweets", "nasa", "--limit", "5", "-f", "json"]),
    ("x", "search", "hk weather",
     [BIN, "--profile", "nora", "twitter", "search", "hk weather", "--limit", "5", "-f", "json"]),
    # LinkedIn (DEC-201): the three profile commands carry the person as
    # `--profile-url`, ALWAYS (without it OpenCLI reads the logged-in account).
    ("linkedin", "profile", "williamhgates",
     [BIN, "--profile", "nora", "linkedin", "profile-read", "--profile-url",
      "https://www.linkedin.com/in/williamhgates/", "-f", "json"]),
    ("linkedin", "experience", "williamhgates",
     [BIN, "--profile", "nora", "linkedin", "profile-experience", "--profile-url",
      "https://www.linkedin.com/in/williamhgates/", "-f", "json"]),
    ("linkedin", "posts", "williamhgates",
     [BIN, "--profile", "nora", "linkedin", "posts", "--profile-url",
      "https://www.linkedin.com/in/williamhgates/", "--limit", "5", "-f", "json"]),
    ("linkedin", "company", "nvidia",
     [BIN, "--profile", "nora", "linkedin", "company", "nvidia", "-f", "json"]),
    # `people-search`, never OpenCLI's `linkedin search` (jobs).
    ("linkedin", "search", "satya nadella",
     [BIN, "--profile", "nora", "linkedin", "people-search", "satya nadella",
      "--limit", "5", "-f", "json"]),
]


@pytest.mark.parametrize(
    ("platform", "what", "target", "expected_argv"), _ARGV_CASES,
)
async def test_an_allowed_combo_invokes_the_exact_argv(
    bridge, platform, what, target, expected_argv,
):
    runner, session, base = bridge
    status, body = await _read(
        session, base,
        {"platform": platform, "what": what, "target": target, "limit": 5},
    )
    assert status == 200
    assert body["status"] == "ok"
    assert runner.calls == [expected_argv]


def test_every_allowlisted_command_has_a_validator():
    """Validation is TOTAL over the allowlist: a command added without a
    validator would be refused (fail closed), and this names it first."""
    from nora.infra.social.bridge_server import _VALIDATORS, _validate_target

    assert set(READ_ONLY_ALLOWLIST.values()) <= set(_VALIDATORS)
    assert _validate_target("linkedin", "search", "anything") is None  # jobs


async def test_no_linkedin_profile_argv_is_built_without_its_profile_url(bridge):
    """Each of the three profile commands reads the LOGGED-IN account when
    `--profile-url` is absent: an argv without it would answer with Nora's
    own account and look like a successful read."""
    runner, session, base = bridge
    for what in ("profile", "experience", "posts"):
        await _read(
            session, base,
            {"platform": "linkedin", "what": what, "target": "williamhgates", "limit": 5},
        )
    assert len(runner.calls) == 3
    for argv in runner.calls:
        i = argv.index("--profile-url")
        assert argv[i + 1] == "https://www.linkedin.com/in/williamhgates/"


async def test_people_search_limit_is_capped_at_its_own_range(bridge):
    runner, session, base = bridge
    await _read(
        session, base,
        {"platform": "linkedin", "what": "search", "target": "someone", "limit": 20},
    )
    argv = runner.calls[0]
    assert argv[argv.index("--limit") + 1] == "10"


async def test_every_allowlisted_pair_is_covered_by_the_argv_test():
    """Guard the guard, DERIVED from the code: the parametrized argv test above
    must cover the WHOLE allowlist — a pair added to the mapping without a case
    here goes red."""
    covered = {(platform, what) for platform, what, _t, _argv in _ARGV_CASES}
    assert set(READ_ONLY_ALLOWLIST) == covered


# ---- target re-validation, host-side ----------------------------------------


@pytest.mark.parametrize(
    ("platform", "what", "target"),
    [
        ("x", "post", "https://example.com/not-a-tweet"),   # not a status URL
        ("x", "post", "not-a-tweet-id"),
        ("x", "profile", "not a username!"),                # charset violation
        ("x", "profile", "@nasa"),                          # no @ prefix
        ("x", "posts", "a" * 31),                           # too long
        ("x", "profile", "../etc/passwd"),                  # path-shaped
        ("x", "search", "   "),                             # empty query
        ("x", "search", "q" * 201),                         # over the cap
        # LinkedIn (DEC-201): an id or slug is checked whole, ASCII only.
        ("linkedin", "profile", "ab"),                      # too short
        ("linkedin", "profile", "william gates"),           # space
        ("linkedin", "profile", "%E5%BC%A0"),               # percent-encoded
        ("linkedin", "posts", "https://www.linkedin.com/in/x/"),  # a URL
        ("linkedin", "experience", "../me"),                # path-shaped
        ("linkedin", "company", "nvidia corp"),
        ("linkedin", "search", "a\nb"),                     # control character
        ("linkedin", "search", "q" * 201),
        # A target that starts with a dash would reach OpenCLI's argv as a
        # FLAG: `/company/-f/` would collide with the bridge's own `-f json`.
        ("linkedin", "company", "-f"),
        ("linkedin", "company", "--evil"),
        ("linkedin", "search", "--limit"),
        ("linkedin", "search", "-f json"),
        ("x", "search", "--help"),
    ],
)
async def test_a_bad_target_is_refused_without_spawning(bridge, platform, what, target):
    runner, session, base = bridge
    status, body = await _read(
        session, base,
        {"platform": platform, "what": what, "target": target, "limit": 5},
    )
    assert status == 400
    assert body["status"] == "unavailable"
    assert runner.calls == []


# ---- exit-code mapping -------------------------------------------------------


async def test_exit_0_with_valid_json_is_ok_with_provenance(bridge):
    runner, session, base = bridge
    runner.stdout = '{"text": "hello"}'
    status, body = await _read(
        session, base,
        {"platform": "x", "what": "post", "target": "1234567890", "limit": 5},
    )
    assert status == 200
    assert body["status"] == "ok"
    assert body["data"] == {"text": "hello"}
    assert body["source_url"] == "https://x.com/i/status/1234567890"


async def test_exit_0_with_malformed_json_is_unavailable(bridge):
    runner, session, base = bridge
    runner.stdout = "not json at all"
    status, body = await _read(
        session, base,
        {"platform": "x", "what": "profile", "target": "nasa", "limit": 5},
    )
    assert status == 200
    assert body["status"] == "unavailable"


@pytest.mark.parametrize("stdout", ["[]", "{}", "null", " [ ] "])
async def test_exit_0_with_an_empty_result_is_nothing_found(bridge, stdout):
    """`opencli twitter thread <an id that names no post>` exits 0 and prints
    `[]`. That is «the read ran and found nothing», not an `ok`: an `ok` with
    no data reaches the model as an empty post it is asked to describe."""
    runner, session, base = bridge
    runner.stdout = stdout
    _status, body = await _read(
        session, base,
        {"platform": "x", "what": "post", "target": "1841173340357800397", "limit": 5},
    )
    assert body == {"status": "nothing_found"}


async def test_exit_66_is_nothing_found(bridge):
    runner, session, base = bridge
    runner.returncode = 66
    _status, body = await _read(
        session, base,
        {"platform": "x", "what": "profile", "target": "nasa", "limit": 5},
    )
    assert body["status"] == "nothing_found"


@pytest.mark.parametrize("code", [69, 77, 1, None])
async def test_other_exits_and_a_timeout_are_unavailable(bridge, code):
    """69 (EX_UNAVAILABLE), 77 (EX_NOPERM — not logged in), anything else, and
    None (the runner's timeout/spawn-failure marker) all mean «could not
    check» (R-27: an error is never a pass)."""
    runner, session, base = bridge
    runner.returncode = code
    _status, body = await _read(
        session, base,
        {"platform": "x", "what": "profile", "target": "nasa", "limit": 5},
    )
    assert body["status"] == "unavailable"


async def test_stderr_is_never_echoed_into_the_response(bridge):
    """stderr can carry session detail; the response is read by a model, so it
    gets neither the text nor a derivation of it."""
    runner, session, base = bridge
    runner.returncode = 1
    runner.stderr = "session cookie=SECRETVALUE expired"
    _status, body = await _read(
        session, base,
        {"platform": "x", "what": "profile", "target": "nasa", "limit": 5},
    )
    assert "SECRETVALUE" not in str(body)
    assert "cookie" not in str(body)


async def test_a_configured_profile_is_the_one_every_read_names():
    """OpenCLI's DEFAULT profile is whoever ran `profile use` last (on norty,
    the operator's own Chrome, which another agent reads through). Every read
    names the configured profile right after the binary, so none can fall
    through to the default."""
    runner = _FakeRunner()
    server = TestServer(
        SocialBridge(opencli_bin=BIN, opencli_profile="nora-alt", runner=runner).app())
    await server.start_server()
    session = ClientSession()
    try:
        base = str(server.make_url("")).rstrip("/")
        status, _ = await _read(
            session, base, {"platform": "x", "what": "profile", "target": "nasa", "limit": 5})
        assert status == 200
        assert runner.calls[0][:3] == [BIN, "--profile", "nora-alt"]
    finally:
        await session.close()
        await server.close()

def test_a_bridge_without_a_profile_refuses_to_start():
    with pytest.raises(ValueError):
        SocialBridge(opencli_bin=BIN, opencli_profile="  ")
