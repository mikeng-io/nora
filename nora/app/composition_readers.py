from __future__ import annotations

import functools
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from nora.agent.dreamer import HomeRoom
from nora.app.gatherings import screened_area
from nora.app.startup import StartupError
from nora.config import Config
from nora.domain.ports import ExternalFilesSource, ReverseGeocoder, SearchProvider
from nora.infra.google.auth import GoogleCredentials

"""Policy- and persons-derived readers for the composition root."""


log = logging.getLogger("nora.app.composition")


def search_provider(cfg: Config)-> SearchProvider | None:

    """The configured web search backend, or None when it is off or has no key."""
    search_client: SearchProvider | None = None
    if cfg.search.enabled:
        search_key = cfg.credentials.get(cfg.search.api_key_env, "")
        if search_key and cfg.search.provider == "exa":
            from nora.infra.search import ExaSearchProvider

            search_client = ExaSearchProvider(
                api_key=search_key,
                url=cfg.search.exa.url,
                contents_url=cfg.search.exa.contents_url,
                search_type=cfg.search.exa.type,
                max_results=cfg.search.max_results,
                content_max_chars=cfg.search.content_max_chars,
                extract_max_chars=cfg.search.extract_max_chars,
                fetch_max_age_hours=cfg.search.exa.fetch_max_age_hours,
                timeout_seconds=cfg.search.timeout_seconds,
            )
        elif search_key:
            from nora.infra.search import TavilySearchProvider

            search_client = TavilySearchProvider(
                api_key=search_key,
                url=cfg.search.tavily.url,
                max_results=cfg.search.max_results,
                search_depth=cfg.search.tavily.search_depth,
                content_max_chars=cfg.search.content_max_chars,
                timeout_seconds=cfg.search.timeout_seconds,
                extract_url=cfg.search.tavily.extract_url,
                extract_depth=cfg.search.tavily.extract_depth,
                extract_max_chars=cfg.search.extract_max_chars,
                min_score=cfg.search.tavily.min_score,
            )
        else:
            log.warning(
                "search enabled on provider %s but %s is unset — web_search stays unavailable",
                cfg.search.provider,
                cfg.search.api_key_env,
            )
    return search_client


def fetch_provider(cfg: Config)-> SearchProvider | None:

    """The backend `web_fetch` extracts through, or `None` to reuse `provider`.

    `None` is the un-configured state (`fetch_provider` unset or equal to
    `provider`): the composition then hands the ONE search client to both
    verbs, exactly as before. A keyless configured backend warns and falls
    back the same way, so a wiring gap degrades to the search backend's own
    ceiling rather than to `unavailable`.
    """
    name = cfg.search.fetch_provider
    if not cfg.search.enabled or not name or name == cfg.search.provider:
        return None
    if name == "exa":
        key_env = cfg.search.exa.api_key_env
        key = cfg.credentials.get(key_env, "")
        if not key:
            log.warning(
                "fetch_provider exa configured but its key env is unset — "
                "falling back to the search backend"
            )
            return None
        from nora.infra.search import ExaSearchProvider

        return ExaSearchProvider(
            api_key=key,
            url=cfg.search.exa.url,
            contents_url=cfg.search.exa.contents_url,
            search_type=cfg.search.exa.type,
            max_results=cfg.search.max_results,
            content_max_chars=cfg.search.content_max_chars,
            extract_max_chars=cfg.search.extract_max_chars,
            fetch_max_age_hours=cfg.search.exa.fetch_max_age_hours,
            timeout_seconds=cfg.search.timeout_seconds,
        )
    key_env = cfg.search.tavily.api_key_env
    key = cfg.credentials.get(key_env, "")
    if not key:
        log.warning(
            "fetch_provider tavily configured but its key env is unset — "
            "falling back to the search backend"
        )
        return None
    from nora.infra.search import TavilySearchProvider

    return TavilySearchProvider(
        api_key=key,
        url=cfg.search.tavily.url,
        max_results=cfg.search.max_results,
        search_depth=cfg.search.tavily.search_depth,
        content_max_chars=cfg.search.content_max_chars,
        timeout_seconds=cfg.search.timeout_seconds,
        extract_url=cfg.search.tavily.extract_url,
        extract_depth=cfg.search.tavily.extract_depth,
        extract_max_chars=cfg.search.extract_max_chars,
        min_score=cfg.search.tavily.min_score,
    )


def google_credentials(cfg: Config, *, metrics: Any)-> GoogleCredentials | None:

    """Nora's ONE Google identity (`[google]`), or
    None. Built when a Google service is switched on (shared files today) and
    all three values `[google]` names are in the environment (DEC-8). A value
    missing is None and a warning naming the variable, never its value: the
    Google services then answer `unavailable`, never the boot. Every service
    gets THIS object and asks it for a token for its own scope; none sees the
    secrets."""
    if not cfg.external_files.enabled:
        return None
    names = (cfg.google.client_id_env, cfg.google.client_secret_env, cfg.google.refresh_token_env)
    values = [cfg.credentials.get(name, "").strip() for name in names]
    missing = [name for name, value in zip(names, values, strict=True) if not value]
    if missing:
        log.warning(
            "[google] %s unset; every Google service answers unavailable (run "
            "scripts/google_auth.py, put the values in .env)",
            ", ".join(missing),
        )
        return None
    client_id, client_secret, refresh_token = values
    return GoogleCredentials(
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=refresh_token,
        metrics=metrics,
    )


def external_files_source(
    cfg: Config, google: GoogleCredentials | None,
) -> ExternalFilesSource | None:
    """The shared-file source the open tool reads through, or None:
    `[external_files]` off, or no Google identity (`google_credentials`). None answers the open
    `unavailable`; recording a link as a handle needs none of this."""
    if not cfg.external_files.enabled or google is None:
        return None
    from nora.infra.external_files.google_drive import GoogleDriveSource

    return GoogleDriveSource(credentials=google)


def scope_home_rooms(allowlist: Any)-> dict[str, HomeRoom]:

    """`conversation id -> the room it names`, for every id form in policy.

    A scope-keyed adaptor's key IS a conversation id, so this map is the whole
    authorization context for its home-room grant. Two rooms can answer to the
    same string (numeric ids are one id space per platform, but policy is not):
    silently keeping the last one would run one unit's adaptor in another's
    room. Refuse the collision at startup instead; a legacy policy that needs
    both rooms must disambiguate them in `policy.toml` first.
    """
    out: dict[str, HomeRoom] = {}
    seen: dict[str, tuple[str, str, str]] = {}
    for conv in allowlist.rooms():
        home = HomeRoom(
            unit=conv.unit,
            platform=conv.platform,
            conversation_id=conv.primary_id,
            conversation_type="group",
            location_trusted=conv.location,
        )
        identity = (conv.unit, conv.platform, conv.primary_id)
        for cid in conv.ids:
            prior = seen.get(cid)
            if prior is not None and prior != identity:
                raise StartupError(
                    "two policy rooms share a conversation id "
                    f"({prior[1]} in {prior[0]!r} and {conv.platform} in "
                    f"{conv.unit!r}); an id must name one room"
                )
            seen[cid] = identity
            out[cid] = home
    return out


def social_person_units(allowlist: Any)-> dict[str, tuple[str, ...]]:

    """Each person LISTED by id in a room, and the units of the rooms that list
    them, in policy order: the units a social item of theirs is charged to and
    announced in. A `*` room lists nobody, the rule `SocialSubjectIndex.members`
    applies to who is polled."""
    out: dict[str, list[str]] = {}
    # ponytail: a person's units are a FILE fact — a guild entry's `persons` lists them for
    # every channel it expands into — so `conversations` is the source here; `rooms()` would
    # drop them whenever the directory cannot enumerate the guild.
    for conv in allowlist.conversations:
        if conv.persons is None or not conv.unit:
            continue
        for person in sorted(conv.persons):
            units = out.setdefault(person, [])
            if conv.unit not in units:
                units.append(conv.unit)
    return {person: tuple(units) for person, units in out.items()}


def social_person_rooms(allowlist: Any)-> dict[tuple[str, str], tuple[str, str]]:

    """`(person, unit) -> (platform, conversation_id)`: the room a social
    stimulus of theirs binds in that unit, the unit's home room when it lists
    them and otherwise the first room of the unit that does, in policy order.
    A social item is shown only in a room that lists its author (the `social`
    lineage predicate), so the session reads it through a room that may be
    shown it."""
    homes = allowlist.home_rooms()
    out: dict[tuple[str, str], tuple[str, str]] = {}
    listing = [c for c in allowlist.rooms() if c.persons is not None and c.unit and c.absorbed]
    for conv in sorted(listing, key=lambda c: homes.get(c.unit) is not c):
        for person in conv.persons:
            out.setdefault((person, conv.unit), (conv.platform, conv.primary_id))
    return out


def social_unit_rooms(allowlist: Any)-> dict[str, tuple[tuple[str, str], ...]]:

    """`unit -> every (platform, conversation_id)` of its rooms, sorted: the
    rooms the social poller may read an item through. `rooms()`, not
    `conversations`, so a channel discovered through a server entry counts."""
    return {
        unit: tuple(
            (c.platform, rid) for c in allowlist.rooms() if c.unit == unit for rid in sorted(c.ids)
        )
        for unit in {c.unit for c in allowlist.rooms() if c.unit}
    }


def _story_frames_embedded(
    engine: AsyncEngine, model: str,
) -> Callable[[Sequence[tuple[str, str]]], Awaitable[set[tuple[str, str]]]]:
    """The memory index's answer to which story frames are embedded under the"""
    from nora.infra.sql.memory_index import story_frame_embedded

    async def embedded(frames: Sequence[tuple[str, str]]) -> set[tuple[str, str]]:

        async with engine.connect() as conn:
            return await story_frame_embedded(conn, frames, model=model)

    return embedded


def reverse_geocoder(
    cfg: Config, environ: Mapping[str, str], cache_store: Any,
) -> ReverseGeocoder | None:
    """The on-demand reverse-geocoder `[presence] geocode_provider` names, or
    `None` when that backend's credential (read from `environ` under the env
    var the config names) or endpoint is absent. Independent of `[presence]
    enabled`: `reverse_geocode` holds it whatever the reader's state. A name of
    its own so a composition's geocoder can be checked without driving a turn
    (`tests/unit/test_eval_full_restore.py`)."""
    if cfg.presence.geocode_provider == "hkgov":
        hkgov_token = environ.get(cfg.presence.hkgov_token_env, "")
        if hkgov_token and cfg.presence.hkgov_geocode_url:
            from nora.infra.geocode_hkgov import HKGovGeocoder

            return HKGovGeocoder(
                service_url=cfg.presence.hkgov_geocode_url,
                token=hkgov_token,
                lang_zh=cfg.presence.hkgov_lang_zh,
                lang_en=cfg.presence.hkgov_lang_en,
                cache_store=cache_store,
                area_screen=functools.partial(
                    screened_area, content_checks=cfg.content_checks.enabled
                ),
            )
        return None
    maps_key = environ.get(cfg.presence.geocode_api_key_env, "")
    if maps_key:
        from nora.infra.geocode import GoogleGeocoder

        return GoogleGeocoder(
            api_key=maps_key,
            cache_store=cache_store,
            area_screen=functools.partial(screened_area, content_checks=cfg.content_checks.enabled),
        )
    return None
