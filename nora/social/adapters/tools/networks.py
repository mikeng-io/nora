from __future__ import annotations

import logging
import urllib.parse
from types import SimpleNamespace
from typing import Literal, cast

from nora.agent.link_owners import OWNER_LINK_INPUT as _OWNER_LINK_INPUT
from nora.agent.link_owners import redirect as _link_redirect
from nora.agent.security import looks_like_coordinate as _looks_like_coordinate
from nora.agent.security import scan_identifiers
from nora.agent.toolkit.files import _other_link_owner
from nora.agent.toolkit.pages import _URL_AND_LINK_DIFFER, _resolve_url_alias, _with_scheme
from nora.agent.toolkit.results import _record, _with_note
from nora.domain.linkedin_links import is_handle as _is_linkedin_handle
from nora.domain.linkedin_links import parse_link as _parse_linkedin_link
from nora.domain.x_links import is_handle as _is_x_handle
from nora.domain.x_links import parse_link as _parse_x_link
from nora.social.adapters.tools.shared import (
    _LINKEDIN_FAILED,
    _LINKEDIN_OFF,
    _LINKEDIN_PERSON_READS,
    _LINKEDIN_READ_NOTES,
    _LINKEDIN_SEARCH_LIMIT_MAX,
    _LINKEDIN_SEARCH_NOTES,
    _LINKEDIN_UNASKED,
    _SOCIAL_LIMIT_MAX,
    _SOCIAL_QUERY_MAX_CHARS,
    _X_LINK_READS,
    _X_NOTES,
    _X_OFF,
    _X_READ_WHATS,
)

log = logging.getLogger("nora.tools")


def _make_x_read(deps):
    async def x_read(
        what: Literal["profile", "posts"] | None = None,
        handle: str | None = None,
        link: str | None = None,
        url: str | None = None,
        limit: int = 5,
    ) -> dict:
        """Read an X (Twitter) post, profile or recent posts, by link or
        handle. Call this when somebody pastes an X link or asks about an X
        account.

        Give exactly one of `handle` or `link`. A link decides what is read:
        a post link reads the post and its thread, an account link reads the
        profile, or its recent posts with `what="posts"`. A handle needs
        `what`. It can only read: it cannot post, like, follow or reply.
        Images and videos come back as links; to see one, call
        `media_fetch(url=...)` on it.

        `status` is `ok`, `nothing_found`, `unavailable` or `refused`. Every
        result carries a `note` saying how to report it.

        Args:
            what: `profile` or `posts`. Leave it out with a post link.
            handle: an X username someone typed, with or without the @.
            link: an X post or account link, as pasted.
            url: same as link.
            limit: how many posts to return with `posts` (1 to 20, default 5).
        """
        c = SimpleNamespace(
            what=what,
            handle=handle,
            link=link,
            url=url,
            limit=limit,
            content_checks=deps.content_checks,
            metrics=deps.metrics,
            social_source=deps.social_source,
            unwired_owners=deps.unwired_owners,
        )
        k_, _, v_ = await _x_read__p1(c)
        if k_:
            return v_
        k_, _, v_ = await _x_read__p2(c)
        if k_:
            return v_
        return None  # type: ignore[return-value]

    return x_read


def _make_x_search(deps):
    content_checks = deps.content_checks
    metrics = deps.metrics
    social_source = deps.social_source

    async def x_search(query: str, limit: int = 5) -> dict:
        """Search X (Twitter) for posts matching free text. Call this when
        somebody wants to know what people on X are saying about something.

        It finds posts, never an account's own page: to read an account, or
        a post someone linked, use `x_read`.

        `status` is `ok`, `nothing_found`, `unavailable` or `refused`. Every
        result carries a `note` saying how to report it.

        Args:
            query: what to search for, in plain words.
            limit: how many posts to return (1 to 20, default 5).
        """
        text = query.strip() if isinstance(query, str) else ""
        # A leading dash is refused as the bridge refuses it: the query is a
        # bare command-line word on the host, where it would read as a flag.
        if not text or len(text) > _SOCIAL_QUERY_MAX_CHARS or text.startswith("-"):
            # Refused HERE, so nothing is sent. `unavailable` («I couldn't
            # search that»), never `nothing_found`, which would claim nothing
            # matched. The query is not echoed back.
            _record(metrics, "x_search", "unavailable", reason="query")
            return {
                "status": "unavailable",
                "note": "I need a short search query in plain words. Ask what to search.",
            }
        # The egress screen: a phone number or ID, or a raw
        # coordinate, must not leave in a search. Decoded first, so a
        # percent-encoded identifier cannot ride past a raw-string match.
        decoded = urllib.parse.unquote(text)
        if scan_identifiers(decoded, content_checks=content_checks) is not None or (
            content_checks and _looks_like_coordinate(decoded)
        ):
            _record(metrics, "x_search", "refused", reason="egress")
            return {
                "status": "refused",
                "note": "I won't put a phone number, ID or coordinate into a "
                "search. Ask what to search for in plain words.",
            }
        try:
            limit = max(1, min(int(limit), _SOCIAL_LIMIT_MAX))
        except (TypeError, ValueError):
            limit = 5
        if social_source is None:
            _record(metrics, "x_search", "unavailable")
            return dict(_X_OFF)
        try:
            out = await social_source.read(
                platform="x",
                what="search",
                target=text,
                limit=limit,
            )
        except Exception:
            log.exception("x_search failed")
            out = dict(_X_OFF)
        # The query is on no log line here, like every search's.
        _record(metrics, "x_search", out["status"])
        return _with_note(out, _X_NOTES)

    return x_search


def _make_linkedin_read(deps):
    async def linkedin_read(
        what: Literal["profile", "experience", "posts", "company"] | None = None,
        handle: str | None = None,
        link: str | None = None,
        url: str | None = None,
        limit: int = 5,
    ) -> dict:
        """Read a LinkedIn profile, work history, recent posts or company
        page, by link or handle. Call this when somebody pastes a LinkedIn
        link or asks about someone's LinkedIn.

        Give exactly one of `handle` or `link`. A person's link or handle
        reads their profile, or `what="experience"` for their roles or
        `what="posts"` for their recent posts. A company is read from its
        link only. One post cannot be read from its link. Images come back
        as links; to see one, call `media_fetch(url=...)` on it.

        `status` is `ok`, `nothing_found`, `unavailable` or `refused`. Every
        result carries a `note` saying how to report it.

        Args:
            what: `profile`, `experience`, `posts` or `company`. Leave it
                out for a profile or a company link.
            handle: a LinkedIn profile id someone typed (the part after /in/).
            link: a LinkedIn profile or company link, as pasted.
            url: same as link.
            limit: how many posts to return with `posts` (1 to 20, default 5).
        """
        c = SimpleNamespace(
            what=what,
            handle=handle,
            link=link,
            url=url,
            limit=limit,
            content_checks=deps.content_checks,
            dream_adaptor=deps.dream_adaptor,
            fire_turn=deps.fire_turn,
            linkedin_source=deps.linkedin_source,
            metrics=deps.metrics,
            unwired_owners=deps.unwired_owners,
        )
        k_, _, v_ = await _linkedin_read__p1(c)
        if k_:
            return v_
        k_, _, v_ = await _linkedin_read__p2(c)
        if k_:
            return v_
        k_, _, v_ = await _linkedin_read__p3(c)
        if k_:
            return v_
        return None  # type: ignore[return-value]

    return linkedin_read


def _make_linkedin_search(deps):
    content_checks = deps.content_checks
    dream_adaptor = deps.dream_adaptor
    fire_turn = deps.fire_turn
    linkedin_source = deps.linkedin_source
    metrics = deps.metrics

    async def linkedin_search(query: str, limit: int = 5) -> dict:
        """Search LinkedIn for people by name or keywords. Call this when
        somebody wants to find a person on LinkedIn.

        Each match gives the name, headline, location and profile link. Some
        share a name and headline, and some are impostors. To read one, use
        `linkedin_read` with its link.

        `status` is `ok`, `nothing_found`, `unavailable` or `refused`. Every
        result carries a `note` saying how to report it.

        Args:
            query: a name or keywords, as the message said it.
            limit: how many people to return (1 to 10, default 5).
        """
        if fire_turn or dream_adaptor:
            _record(metrics, "linkedin_search", "refused", reason="unasked")
            return dict(_LINKEDIN_UNASKED)
        text = query.strip() if isinstance(query, str) else ""
        # A leading dash is refused as the bridge refuses it: the query is a
        # bare command-line word on the host, where it would read as a flag.
        if not text or len(text) > _SOCIAL_QUERY_MAX_CHARS or text.startswith("-"):
            _record(metrics, "linkedin_search", "unavailable", reason="query")
            return {
                "status": "unavailable",
                "note": "I need a short name or keywords to search for. Ask who to search.",
            }
        # A phone number pasted into a PEOPLE search would be looked up
        # against real people, so the screen matters most here.
        decoded = urllib.parse.unquote(text)
        if scan_identifiers(decoded, content_checks=content_checks) is not None or (
            content_checks and _looks_like_coordinate(decoded)
        ):
            _record(metrics, "linkedin_search", "refused", reason="egress")
            return {
                "status": "refused",
                "note": "I won't put a phone number, ID or coordinate into a "
                "search. Ask for a name instead.",
            }
        try:
            limit = max(1, min(int(limit), _LINKEDIN_SEARCH_LIMIT_MAX))
        except (TypeError, ValueError):
            limit = 5
        if linkedin_source is None:
            _record(metrics, "linkedin_search", "unavailable")
            return dict(_LINKEDIN_OFF)
        try:
            out = await linkedin_source.read(
                platform="linkedin",
                what="search",
                target=text,
                limit=limit,
            )
        except Exception:
            log.exception("linkedin_search failed")
            out = dict(_LINKEDIN_FAILED)
        _record(metrics, "linkedin_search", out["status"])
        return _with_note(out, _LINKEDIN_SEARCH_NOTES)

    return linkedin_search


async def _x_read__p1(c):
    c.link, c.differ = _resolve_url_alias("x_read", c.link, c.url, _parse_x_link, c.metrics)
    if c.differ:
        return (True, None, dict(_URL_AND_LINK_DIFFER))
    c.given = [
        name
        for name, value in (("handle", c.handle), ("link", c.link))
        if isinstance(value, str) and value.strip()
    ]
    if len(c.given) != 1:
        _record(c.metrics, "x_read", "unavailable", reason="input")
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": ("Give exactly one of handle or link, then ask again."),
            },
        )
    (c.source,) = c.given
    if c.source == "link":
        c.text = _with_scheme(cast(str, c.link))
        c.parsed = _parse_x_link(c.text)
        if c.parsed is None:
            c.found = _other_link_owner(c.text, "x_read", c.unwired_owners)
            if c.found is not None:
                c.owner, c.claim = c.found
                _record(
                    c.metrics,
                    "x_read",
                    "nothing_found",
                    reason="link_owner",
                    owner=c.owner.network,
                    kind=c.claim.kind,
                )
                return (True, None, _link_redirect(c.owner, c.claim, _OWNER_LINK_INPUT))
            _record(c.metrics, "x_read", "unavailable", reason="link")
            return (
                True,
                None,
                {
                    "status": "unavailable",
                    "note": (
                        "That isn't an X post or "
                        "account link I can read. Say so; don't guess what it shows."
                    ),
                },
            )
        c.read = _X_LINK_READS[c.parsed.kind]
        if c.parsed.kind == "profile" and c.what == "posts":
            c.read = "posts"
        elif c.what is not None and c.what != c.read:
            _record(c.metrics, "x_read", "unavailable", reason="what")
            return (
                True,
                None,
                {
                    "status": "unavailable",
                    "note": (
                        "The link already says what to "
                        "read. Call again with the link alone, or what=posts with an account link."
                    ),
                },
            )
        c.target = c.parsed.key
    else:
        if c.what not in _X_READ_WHATS:
            _record(c.metrics, "x_read", "unavailable", reason="what")
            return (
                True,
                None,
                {
                    "status": "unavailable",
                    "note": (
                        "With a handle, say what to "
                        "read: profile or posts. A single post is read from its link."
                    ),
                },
            )
        c.name = cast(str, c.handle).strip()
        if c.name.startswith("@"):
            c.name = c.name[1:]
        if not _is_x_handle(c.name):
            _record(c.metrics, "x_read", "unavailable", reason="handle")
            return (
                True,
                None,
                {
                    "status": "unavailable",
                    "note": (
                        "That isn't an X username I can "
                        "look up (letters, digits and underscores, at most 15). Ask for it again."
                    ),
                },
            )
        c.read = c.what
        c.target = c.name
    return (False, None, None)


async def _x_read__p2(c):
    if scan_identifiers(c.target, content_checks=c.content_checks) is not None or (
        c.content_checks and _looks_like_coordinate(c.target)
    ):
        _record(c.metrics, "x_read", "refused", reason="egress")
        return (
            True,
            None,
            {
                "status": "refused",
                "note": (
                    "I won't put a phone number, ID or "
                    "coordinate into a lookup. Ask for the account or the link instead."
                ),
            },
        )
    try:
        c.limit = max(1, min(int(c.limit), _SOCIAL_LIMIT_MAX))
    except (TypeError, ValueError):
        c.limit = 5
    if c.social_source is None:
        _record(c.metrics, "x_read", "unavailable")
        return (True, None, dict(_X_OFF))
    try:
        c.out = await c.social_source.read(
            platform="x", what=c.read, target=c.target, limit=c.limit
        )
    except Exception:
        log.exception("x_read failed")
        c.out = dict(_X_OFF)
    _record(c.metrics, "x_read", c.out["status"], what=c.read)
    return (True, None, _with_note(c.out, _X_NOTES))
    return (False, None, None)


async def _linkedin_read__p1(c):
    if c.fire_turn or c.dream_adaptor:
        _record(c.metrics, "linkedin_read", "refused", reason="unasked")
        return (True, None, dict(_LINKEDIN_UNASKED))
    c.link, c.differ = _resolve_url_alias(
        "linkedin_read", c.link, c.url, _parse_linkedin_link, c.metrics
    )
    if c.differ:
        return (True, None, dict(_URL_AND_LINK_DIFFER))
    c.given = [
        name
        for name, value in (("handle", c.handle), ("link", c.link))
        if isinstance(value, str) and value.strip()
    ]
    if len(c.given) != 1:
        _record(c.metrics, "linkedin_read", "unavailable", reason="input")
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": ("Give exactly one of handle or link, then ask again."),
            },
        )
    (c.source,) = c.given
    return (False, None, None)


async def _linkedin_read__p2(c):
    if c.source == "link":
        c.text = _with_scheme(cast(str, c.link))
        c.parsed = _parse_linkedin_link(c.text)
        if c.parsed is None:
            c.found = _other_link_owner(c.text, "linkedin_read", c.unwired_owners)
            if c.found is not None:
                c.owner, c.claim = c.found
                _record(
                    c.metrics,
                    "linkedin_read",
                    "nothing_found",
                    reason="link_owner",
                    owner=c.owner.network,
                    kind=c.claim.kind,
                )
                return (True, None, _link_redirect(c.owner, c.claim, _OWNER_LINK_INPUT))
            _record(c.metrics, "linkedin_read", "unavailable", reason="link")
            return (
                True,
                None,
                {
                    "status": "unavailable",
                    "note": (
                        "That isn't a LinkedIn profile "
                        "or company link I can read. Say so; don't guess what it shows."
                    ),
                },
            )
        if c.parsed.kind == "post":
            _record(c.metrics, "linkedin_read", "unavailable", reason="post")
            return (
                True,
                None,
                {
                    "status": "unavailable",
                    "note": (
                        "I can't open a single LinkedIn "
                        "post from its link. Say so. I can read the author's recent posts from "
                        "their "
                        "profile link."
                    ),
                },
            )
        if c.parsed.kind == "company":
            if c.what not in (None, "company"):
                _record(c.metrics, "linkedin_read", "unavailable", reason="what")
                return (
                    True,
                    None,
                    {
                        "status": "unavailable",
                        "note": (
                            "A company link reads the company page. Call again with the link alone."
                        ),
                    },
                )
            c.read = "company"
        else:
            c.read = c.what or "profile"
            if c.read not in _LINKEDIN_PERSON_READS:
                _record(c.metrics, "linkedin_read", "unavailable", reason="what")
                return (
                    True,
                    None,
                    {
                        "status": "unavailable",
                        "note": (
                            "A person's link reads their profile, experience or posts. Say which."
                        ),
                    },
                )
        c.target = c.parsed.key
    else:
        if c.what not in _LINKEDIN_PERSON_READS:
            _record(c.metrics, "linkedin_read", "unavailable", reason="what")
            return (
                True,
                None,
                {
                    "status": "unavailable",
                    "note": (
                        "With a handle, say what to "
                        "read: profile, experience or posts. A company is read from its link."
                    ),
                },
            )
        c.name = cast(str, c.handle).strip().removeprefix("@")
        if not _is_linkedin_handle(c.name):
            _record(c.metrics, "linkedin_read", "unavailable", reason="handle")
            return (
                True,
                None,
                {
                    "status": "unavailable",
                    "note": (
                        "That isn't a LinkedIn profile "
                        "id I can look up (letters, digits and hyphens). Ask for their profile "
                        "link "
                        "instead."
                    ),
                },
            )
        c.read = c.what
        c.target = c.name
    if scan_identifiers(c.target, content_checks=c.content_checks) is not None or (
        c.content_checks and _looks_like_coordinate(c.target)
    ):
        _record(c.metrics, "linkedin_read", "refused", reason="egress")
        return (
            True,
            None,
            {
                "status": "refused",
                "note": (
                    "I won't put a phone number, ID or "
                    "coordinate into a lookup. Ask for their profile link instead."
                ),
            },
        )
    try:
        c.limit = max(1, min(int(c.limit), _SOCIAL_LIMIT_MAX))
    except (TypeError, ValueError):
        c.limit = 5
    if c.linkedin_source is None:
        _record(c.metrics, "linkedin_read", "unavailable")
        return (True, None, dict(_LINKEDIN_OFF))
    return (False, None, None)


async def _linkedin_read__p3(c):
    try:
        c.out = await c.linkedin_source.read(
            platform="linkedin", what=c.read, target=c.target, limit=c.limit
        )
    except Exception:
        log.exception("linkedin_read failed")
        c.out = dict(_LINKEDIN_FAILED)
    _record(c.metrics, "linkedin_read", c.out["status"], what=c.read)
    return (True, None, _with_note(c.out, _LINKEDIN_READ_NOTES))
    return (False, None, None)
