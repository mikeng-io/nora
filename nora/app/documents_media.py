"""The media and map grounding pipeline a published document runs through.

`resolve_media` rewrites every Image's room-scoped media code to its full
sha256 — a stored document holds content addresses, never the short code a
model typed. `ground_maps` fills every Map place's coordinates from its NAME,
through the same outbound and return-path screens `place_info` runs. Both
answer `(document, findings, down)`: `down` marks a store or locator failure,
which the caller answers `unavailable`, never a pass.
"""

from __future__ import annotations

import asyncio
import copy
import re
from collections.abc import Callable
from typing import Any

from nora.agent.egress import scan_identifiers
from nora.agent.security import PlaceLocator, screen_and_locate_place
from nora.documents.check import Finding
from nora.domain.media import code_matches, parse_media_type, resolve_room_media, room_sightings
from nora.domain.ports import MediaObjectStore, RecordStore

_HEX = re.compile(r"^[0-9a-f]{6,64}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_HANDLES_SHOWN = 5

#: How many places one publish looks up at once. Each is a billed Places call, so the
#: fan-out is bounded even though a Map may name up to 50.
_LOCATE_CONCURRENCY = 5

#: How many DISTINCT places one publish may look up, over every Map in it. Each is a billed
#: Places call, and the schema bounds a Map at 50 but not how many Maps a document holds.
_MAX_PLACES_PER_PUBLISH = 50

#: Findings a Map can return, like the checker's own cap, so 50 unknown names are not 50 lines.
_MAX_PLACE_FINDINGS = 10


def document_images(document: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Every Image node and its JSON Pointer. Iterative: the document has passed the
    checker, so depth is bounded, but nothing here needs to rely on that."""
    found: list[tuple[str, dict[str, Any]]] = []
    stack: list[tuple[dict[str, Any], str]] = [(document["root"], "/root")]
    while stack:
        node, where = stack.pop()
        if node.get("type") == "Image":
            found.append((where, node))
        for index, child in enumerate(node.get("children") or []):
            stack.append((child, f"{where}/children/{index}"))
    return sorted(found, key=lambda pair: pair[0])


def document_maps(document: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Every Map node and its JSON Pointer, in document order (see `document_images`)."""
    found: list[tuple[str, dict[str, Any]]] = []
    stack: list[tuple[dict[str, Any], str]] = [(document["root"], "/root")]
    while stack:
        node, where = stack.pop()
        if node.get("type") == "Map":
            found.append((where, node))
        for index, child in enumerate(node.get("children") or []):
            stack.append((child, f"{where}/children/{index}"))
    return sorted(found, key=lambda pair: pair[0])


async def sightings(
    *,
    records: RecordStore,
    media: MediaObjectStore,
    room_ids: Callable[[str, str], frozenset[str]],
    now_ms: Callable[[], int],
    window: int,
    platform: str,
    conversation_id: str,
    code: str,
) -> set[str] | None:
    """The distinct full sha256 of every picture in this room whose sha256
    starts with `code`, or `None` when the room could not be read, or when the
    scan was cut short and found nothing (`could not check that far` is never
    `no such picture`)."""
    found: set[str] = set()
    truncated = False
    try:
        ids = room_ids(platform, conversation_id) | {conversation_id}
        now = now_ms()
        for cid in sorted(ids):
            # The one resolver the sandbox session shares (DEC-251): the recent window
            # plus the room's last 90 days of message rows, keyed by the message row.
            seen = await room_sightings(
                records, conversation_id=cid, now_ms=now, window=window
            )
            truncated = truncated or seen.truncated
            for sha, sighting in code_matches(seen.sightings, code).items():
                # Images only (this block's own rule): a clip or a voice note is not a
                # picture, and a shared item's cover is a preview of someone else's post.
                if (
                    _SHA.match(sha)
                    and not sighting.cover
                    and parse_media_type(sighting.media_type)[0] == "image"
                ):
                    found.add(sha)
            # What Nora made or kept in this room is in the index, not in a message row.
            look = await resolve_room_media(
                media, code=code, platform=platform, conversation_id=cid
            )
            if not look.available:
                return None
            found.update(sha for sha in look.objects if _SHA.match(sha))
    except Exception:  # noqa: BLE001 - a store that is down is `unavailable`, not a guess
        return None
    if truncated and not found:
        return None
    return found


async def resolve_media(
    *,
    records: RecordStore,
    media: MediaObjectStore,
    room_ids: Callable[[str, str], frozenset[str]],
    now_ms: Callable[[], int],
    window: int,
    platform: str,
    conversation_id: str,
    document: dict[str, Any],
) -> tuple[dict[str, Any], tuple[str, ...], list[Finding], bool]:
    """Rewrite every Image's code to its full sha256. Returns the rewritten
    document, the shas it names, the findings, and whether a store was down."""
    rewritten = copy.deepcopy(document)
    findings: list[Finding] = []
    named: list[str] = []
    cache: dict[str, str | None] = {}
    for where, node in document_images(rewritten):
        code = str(node["props"].get("media", ""))
        path = f"{where}/props/media"
        if not _HEX.match(code):
            findings.append(
                Finding(path, "media_code", "must be a media code: 6 to 64 hex characters")
            )
            continue
        if code not in cache:
            seen = await sightings(
                records=records, media=media, room_ids=room_ids, now_ms=now_ms,
                window=window, platform=platform, conversation_id=conversation_id,
                code=code,
            )
            if seen is None:
                return document, (), [], True
            if len(seen) == 1:
                cache[code] = next(iter(seen))
            else:
                cache[code] = None
                shown = ", ".join(sorted(s[:8] for s in seen)[:_HANDLES_SHOWN])
                if not seen:
                    message = f"no picture with code {code} in this room's recent messages"
                    findings.append(Finding(path, "media_unknown", message))
                else:
                    message = (
                        f"{code} matches {len(seen)} pictures in this room ({shown}); "
                        "use a longer code"
                    )
                    findings.append(Finding(path, "media_ambiguous", message))
                continue
        sha = cache[code]
        if sha is None:
            findings.append(
                Finding(path, "media_unknown", f"no single picture for code {code}")
            )
        else:
            node["props"]["media"] = sha
            named.append(sha)
    return rewritten, tuple(sorted(set(named))), findings, False


async def ground_maps(
    *,
    place_locator: PlaceLocator | None,
    content_checks: bool,
    document: dict[str, Any],
    person_names: frozenset[str] | None,
    live_addresses: frozenset[str] | None = None,
) -> tuple[dict[str, Any], list[Finding], bool]:
    """Fill every Map place's coordinate from its NAME.

    The model writes names and never a coordinate (DEC-24), so whatever `lat`, `lng` or
    `route` a spec carries is DISCARDED and the pair is looked up here, through the same
    outbound and return-path screens `place_info` runs: a person's name, an identifier or a
    coordinate in the name is refused before any call. A place that does not resolve to
    exactly one is a finding that names it, never a guess. Returns the document, the
    findings, and whether the locator was down."""
    rewritten = copy.deepcopy(document)
    maps = document_maps(rewritten)
    if not maps:
        return rewritten, [], False
    findings: list[Finding] = []
    resolved: dict[str, dict[str, Any] | None] = {}
    gate = asyncio.Semaphore(_LOCATE_CONCURRENCY)
    live = {a.strip().casefold() for a in (live_addresses or ()) if a.strip()}

    def key(name: str) -> str:
        return " ".join(name.split()).casefold()

    async def look(name: str) -> None:
        folded = key(name)
        if any(a in folded or folded in a for a in live):
            # R-57: an address `reverse_geocode` gave out THIS turn is a live position, the
            # one `route` already refuses; naming it on a Map would pin it in a document.
            resolved[key(name)] = {"status": "refused", "reason": "live_position"}
            return
        async with gate:
            got = await screen_and_locate_place(
                name, place_locator=place_locator, person_names=person_names,
                content_checks=content_checks,
            )
        resolved[key(name)] = got.data if got.status == "resolved" else {"status": got.status}
        if got.status == "refused":
            resolved[key(name)] = {"status": "refused", "reason": got.reason}

    first_spelling: dict[str, str] = {}
    for _, m in maps:
        for p in m["props"]["places"]:
            first_spelling.setdefault(key(str(p.get("name", ""))), str(p.get("name", "")))
    if len(first_spelling) > _MAX_PLACES_PER_PUBLISH:
        return rewritten, [Finding(
            "", "place_limit",
            f"a document may map at most {_MAX_PLACES_PER_PUBLISH} different places in total, "
            f"and this one names {len(first_spelling)}; keep the ones that matter",
        )], False
    await asyncio.gather(*(look(n) for n in first_spelling.values()))
    if any((resolved[k] or {}).get("status") == "unavailable" for k in first_spelling):
        return document, [], True
    for where, node in maps:
        props = node["props"]
        props.pop("route", None)
        for index, place in enumerate(props["places"]):
            got = resolved[key(str(place.get("name", "")))] or {}
            path = f"{where}/props/places/{index}"
            status = got.get("status")
            if status == "ok" and "lat" in got and "lng" in got:
                place["lat"], place["lng"] = got["lat"], got["lng"]
                continue
            place.pop("lat", None)
            place.pop("lng", None)
            name = str(place.get("name", ""))
            if status == "ambiguous":
                could = ", ".join(
                    c
                    for c in (str(c) for c in got.get("could_be", [])[:3])
                    if scan_identifiers(c, content_checks=content_checks)
                    is None  # as `place_info` screens its candidates
                )
                findings.append(Finding(
                    path, "place_ambiguous",
                    f"{name!r} matches several places ({could}); name it more fully",
                ))
            elif status == "refused":
                findings.append(Finding(
                    path, "place_refused",
                    "a Map shows public places by name; a person's home or position, an "
                    "identifier or a coordinate cannot be mapped",
                ))
            else:
                findings.append(Finding(
                    path, "place_unknown",
                    f"no place found for {name!r}; name a place a map knows",
                ))
    return rewritten, findings[:_MAX_PLACE_FINDINGS], False
