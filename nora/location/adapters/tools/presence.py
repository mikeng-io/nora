from __future__ import annotations

import logging
from datetime import UTC, datetime
from functools import partial
from types import SimpleNamespace
from typing import Any, Literal
from zoneinfo import ZoneInfo

from nora.agent.room import find_by_name
from nora.agent.toolkit.results import _record
from nora.agent.whereis import (
    STALE_AFTER_MS,
    Whereis,
    _dedupe,
    presence_audience,
)
from nora.domain.mobility_forecast import (
    detect_stay_anomalies,
    trip_patterns,
)

log = logging.getLogger("nora.tools")


def _make_where_is(deps):
    content_checks = deps.content_checks
    geocoder = deps.geocoder
    location_trusted = deps.location_trusted
    metrics = deps.metrics
    presence_circles = deps.presence_circles
    presence_source = deps.presence_source
    refresher = deps.refresher
    room = deps.room

    async def where_is(person: str) -> dict:
        """Where somebody in this conversation is RIGHT NOW: a live reading, not a pattern.
        Call this when somebody asks where a person is.

        Give `person` as they were named. It forces a fresh reading first, so it
        may take a moment; only a member id goes out for that, never a coordinate.

        Returns `status`: ok, refused, not_found, ambiguous or unavailable, with
        a `note` on how to report it.

        Args:
            person: Who they asked about, exactly as they said it.
        """
        if presence_source is None or not presence_circles:
            # Presence not wired (offline/eval compositions, or `[presence]`
            # disabled / identity.toml carrying no circle). `unavailable`, never
            # a silent empty — a wiring gap must look like «I could not check»,
            # not «I don't know where they are» (R-5), and never a crash.
            _record(metrics, "where_is", "unavailable")
            return {
                "status": "unavailable",
                "note": "I can't check where people are right now. Say you have no "
                "way to see that, and do not guess.",
            }
        # One `Whereis` per circle, first `ok` wins. With one circle this is
        # exactly the seam's own behaviour. With several, a non-`refused` answer from
        # a circle the room IS a subset of outranks a `refused` from one it is not
        # (the refusal is about the ROOM/circle pair, not the person), while `ok`
        # from any qualifying circle is the answer. Disclosure is decided by
        # `Whereis.locate`'s guard (the subset check is waivable per room, DEC-74,
        # DEC-79), NOT by this fold: the loop adds no disclosure of its own and can
        # only pick among answers the guard already authorised. `degraded` arrives as
        # `unavailable` rather than `refused`, so it is selected by the non-`refused`
        # rule below; that is inert, since a degraded room degrades every circle
        # identically.
        results: list[dict] = []
        for circle in presence_circles:
            out = await Whereis(
                room=room,
                circle=circle,
                presence=presence_source,
                refresher=refresher,
                geocoder=geocoder,
                location_trusted=location_trusted,
                metrics=metrics,
                content_checks=content_checks,
            ).locate(
                person,
            )
            if str(out.get("status", "")) == "ok":
                _record(metrics, "where_is", "ok", age_seconds=out.get("age_seconds"))
                # `where_is` carries NO address (position + saved place + lat/lng only),
                # so the disclosed-live-address set (R-57) is filled by `reverse_geocode`:
                # the tool that resolves a coordinate into the street address `route`
                # screens for.
                return out
            results.append(out)
        answer = next(
            (r for r in results if r.get("status") != "refused"),
            results[0],
        )
        _record(metrics, "where_is", str(answer.get("status", "unavailable")))
        return answer

    return where_is


def _make__been_clock(deps):
    def _been_clock(minute_of_day: float | None) -> str | None:
        if minute_of_day is None:
            return None
        m = round(minute_of_day) % 1440
        return f"{m // 60:02d}:{m % 60:02d}"

    return _been_clock


def _make__been_iso(deps):
    _been_tz_name = deps._been_tz_name

    def _been_iso(ts_ms: int) -> str:
        """RFC 3339 with the substrate's timezone — the model-readable form of
        a timestamp. DEC-156: unix-ms epoch values are unreadable to the model
        (it spends `calculate` calls converting them), so every timestamp the
        tool emits carries this form alongside the raw ms."""
        return datetime.fromtimestamp(ts_ms / 1000, tz=ZoneInfo(_been_tz_name)).isoformat(
            timespec="seconds"
        )

    return _been_iso


def _make__resolve_bound_place(deps):
    mobility = deps.mobility
    persons = deps.persons
    presence_circles = deps.presence_circles
    _place_words = deps._place_words
    _presence_audience = _make__presence_audience(deps)

    async def _resolve_bound_place(place_id: int) -> tuple[Any, str | None]:
        """A derived place for `route`, or `None` when this room may not be
        told it: the place's person must be in a circle whose audience check
        passes for this room (`_presence_audience`, R-31), exactly as their
        routine would be. Unknown ids and any failure are `None` too."""
        if mobility is None or not presence_circles:
            return None, None
        try:
            place = await mobility.place_by_id(place_id=place_id)
        except Exception:
            log.exception("route bound place read failed")
            return None, None
        if place is None:
            return None, None
        for circle in presence_circles:
            if not any(
                persons.subject_id_for(pid) == place.subject_id for pid in circle.person_ids
            ):
                continue
            _, refusal = await _presence_audience(
                circle,
                None,
                tool="route",
                anyone="where anyone's places are",
                people_phrase="people's places",
            )
            if refusal is None:
                from nora.infra.routes.places import Place

                name = _place_words(place.label, place.kind, place.saved_place_id is not None)
                return Place(
                    place_id=f"derived:{place.id}",
                    name=name,
                    address=name,
                    lat=place.lat,
                    lng=place.lng,
                ), place.label
        return None, None

    return _resolve_bound_place


def _make__presence_audience(deps):
    content_checks = deps.content_checks
    location_trusted = deps.location_trusted
    metrics = deps.metrics
    room = deps.room
    _been_everyone = deps._been_everyone

    async def _presence_audience(
        circle: Any,
        asked: str | None,
        *,
        tool: str,
        anyone: str,
        people_phrase: str,
    ) -> tuple[tuple[Any, ...], dict | None]:
        """Who a DERIVED-presence answer may be about, or why not: `(refs,
        None)` when the guard passes, `((), refusal)` otherwise. ONE copy for
        `where_been` and `routine_read` so the two cannot drift; `anyone`
        and `people_phrase` only word the notes.

        THE SAME three-way fail-closed `Whereis.locate` runs
        (nora/agent/whereis.py), composed directly against `DisclosureGuard`
        so the failure matrix cannot drift from `where_is`'s: available →
        check; degraded → `unavailable` (an ERROR, never a policy answer);
        unnamed or room ⊄ circle → `refused` — unless the room is
        location-trusted (DEC-79), which waives exactly what it waives there
        and is COUNTED when it does."""
        view = await room.scope()
        if not view.available:
            return (), {
                "status": "unavailable",
                "note": "I can't work out who is in this group right now, so "
                f"I can't say {anyone}. Say so rather than guessing.",
            }
        # The decision is `presence_audience`'s, shared with a Cognition
        # rollup line's place release; the branches below word and count it.
        audience = presence_audience(
            view, circle, location_trusted=location_trusted, content_checks=content_checks
        )
        subset_ok = audience != "not_subset"
        unnamed_blocks = audience == "unnamed"
        if view.degraded:
            if metrics is not None:
                metrics.inc(
                    "nora_presence_disclosure_refusals_total",
                    reason="degraded",
                )
            log.warning(
                "%s could not verify the room (degraded): circle=%s location_trusted=%s",
                tool,
                circle.circle_id,
                location_trusted,
            )
            return (), {
                "status": "unavailable",
                "note": "I can't confirm who is in this group right now, so I "
                f"can't say {anyone}. Say you couldn't check, without "
                "guessing or blaming anyone's sharing settings.",
            }
        if unnamed_blocks or not subset_ok:
            # WHICH condition fired, so a refusal is diagnosable from the
            # metric instead of inferred — the same labels `locate` uses.
            reason = "unnamed" if unnamed_blocks else "not_subset"
            if metrics is not None:
                metrics.inc(
                    "nora_presence_disclosure_refusals_total",
                    reason=reason,
                )
            log.warning(
                "%s refused (%s): circle=%s unnamed=%d subset_ok=%s location_trusted=%s",
                tool,
                reason,
                circle.circle_id,
                view.unnamed,
                subset_ok,
                location_trusted,
            )
            return (), {
                "status": "refused",
                "note": f"I can't share {people_phrase} in this conversation: "
                "someone here is outside the location-sharing circle, "
                "or I can't confirm who is here. Say so in one clause "
                "and do not hint at the pattern.",
            }
        # A WAIVED guard is counted, not silent — the same DEC-79 rule
        # `Whereis.locate` runs one branch below its own guard.
        if content_checks and view.unnamed > 0 and location_trusted:
            if metrics is not None:
                metrics.inc(
                    "nora_presence_disclosure_waived_total",
                    reason="unnamed",
                )
            log.warning(
                "%s disclosed into a location-trusted room holding %d "
                "unmapped account(s) (DEC-79 waiver): circle=%s",
                tool,
                view.unnamed,
                circle.circle_id,
            )
        # R-47: who may be ASKED ABOUT resolves against the CIRCLE unioned
        # with the room — the audience gate has already been cleared above.
        # No name (or 「大家」) means the room's own people: the group asking
        # about the group.
        if not asked or asked.casefold() in _been_everyone:
            refs = _dedupe(view.people)
            if not refs:
                return (), {
                    "status": "nothing_found",
                    "note": "I can't see who is in this conversation, so there "
                    "is no derived pattern to report. Say so, and do "
                    "not guess.",
                }
            return tuple(refs), None
        matches = _dedupe(find_by_name(circle.people, asked) + view.find(asked))
        if not matches:
            return (), {
                "status": "not_found",
                "note": "Nobody in this conversation goes by that. Say "
                "you're not sure who they mean, and do not guess.",
            }
        if len(matches) > 1:
            return (), {
                "status": "ambiguous",
                "could_be": [p.name for p in matches],
                "note": "More than one person here goes by that. Ask which one.",
            }
        return (matches[0],), None

    return _presence_audience


def _make_where_been(deps):
    async def where_been(
        what: Literal["day", "places", "rhythm", "routes"],
        name: str | None = None,
        days: int = 7,
        date: str | None = None,
    ) -> dict:
        """Where somebody in this conversation HAS BEEN: today, usually, repeatedly.
        Call this for HISTORY questions about movement patterns.

        Never for where someone is RIGHT NOW; that is `where_is`. A personal
        place name resolves through `saved_places`.

        `what` is `day` (one day's trips and stops), `places` (their usual
        places, each `home`, `work` or `unlabeled`), `rhythm` (the hours of the
        week they usually stop somewhere) or `routes` (repeated trips and their
        corridors). Went to work today? Read `day` with `places`.

        Returns `status`: ok, nothing_found, unavailable, refused, not_found or
        ambiguous, with a `note` on how to report it.

        Args:
            what: Which arm: "day", "places", "rhythm" or "routes".
            name: Who they asked about, exactly as they said it. Leave it out
                for the people in THIS conversation (or pass "everyone"): the
                group asking about the group.
            days: How far back, in days (default 7, capped at 90). Used by
                "rhythm" and "routes"; "places" carries its own first/last
                seen.
            date: For "day" only: the day they mean, "YYYY-MM-DD". Leave out
                for today.
        """
        from nora.location.adapters.tools.routes import _make__bind_place

        c = SimpleNamespace(
            what=what,
            name=name,
            days=days,
            date=date,
            clock=deps.clock,
            content_checks=deps.content_checks,
            metrics=deps.metrics,
            mobility=deps.mobility,
            persons=deps.persons,
            presence_circles=deps.presence_circles,
            presence_source=deps.presence_source,
            _been_anomaly_min_samples=deps._been_anomaly_min_samples,
            _been_anomaly_z=deps._been_anomaly_z,
            _been_arms=deps._been_arms,
            _been_clock=_make__been_clock(deps),
            _been_day_ms=deps._been_day_ms,
            _been_iso=_make__been_iso(deps),
            _been_max_days=deps._been_max_days,
            _been_novelty_radius_m=deps._been_novelty_radius_m,
            _been_tz_name=deps._been_tz_name,
            _bind_place=_make__bind_place(deps),
            _place_words=deps._place_words,
            _presence_audience=_make__presence_audience(deps),
        )
        k_, _, v_ = await _where_been__p1(c)
        if k_:
            return v_
        k_, _, v_ = await _where_been__p2(c)
        if k_:
            return v_
        c._been_read = partial(_where_been__been_read, c)
        k_, _, v_ = await _where_been__p3(c)
        if k_:
            return v_
        k_, _, v_ = await _where_been__p4(c)
        if k_:
            return v_
        k_, _, v_ = await _where_been__p5(c)
        if k_:
            return v_
        return None  # type: ignore[return-value]

    return where_been


async def _where_been__p1(c):
    if str(c.what) not in c._been_arms:
        _record(c.metrics, "where_been", "unavailable", reason="what")
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": (
                    "I can't answer that with "
                    "where_been. It answers a day, usual places, a usual weekly rhythm, or "
                    "repeated routes."
                ),
            },
        )
    if c.mobility is None or not c.presence_circles:
        _record(c.metrics, "where_been", "unavailable")
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": (
                    "I can't check where people have "
                    "been right now. Say you have no way to see that, and do not guess."
                ),
            },
        )
    try:
        c.now_ms = int((c.clock() if c.clock is not None else datetime.now(UTC)).timestamp() * 1000)
        c.window_days = min(max(int(c.days), 0), c._been_max_days)
    except (TypeError, ValueError):
        _record(c.metrics, "where_been", "unavailable", reason="window")
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": ("I couldn't read the window you gave me. Say so, and do not guess one."),
            },
        )
    c.window_ms = c.window_days * c._been_day_ms
    c.day_start_ms = c.day_end_ms = c.day_iso = None
    if c.what == "day":
        c.hk = ZoneInfo(c._been_tz_name)
        try:
            c.day = (
                datetime.strptime(str(c.date).strip(), "%Y-%m-%d").date()
                if c.date and str(c.date).strip()
                else datetime.fromtimestamp(c.now_ms / 1000, tz=c.hk).date()
            )
        except ValueError:
            _record(c.metrics, "where_been", "unavailable", reason="date")
            return (
                True,
                None,
                {
                    "status": "unavailable",
                    "note": (
                        "I couldn't read that date. "
                        "Give it as YYYY-MM-DD, or leave it out for today."
                    ),
                },
            )
        c.day_start_ms = int(
            datetime(c.day.year, c.day.month, c.day.day, tzinfo=c.hk).timestamp() * 1000
        )
        c.day_end_ms = c.day_start_ms + c._been_day_ms
        c.day_iso = c.day.isoformat()

    async def _been_last_recorded(circle, ref) -> int | None:
        """The ts of `ref`'s newest RAW fix — the edge of everything the
        derived facts can know. Read from the same store `where_is` reads;
        None when presence is not wired or nothing was ever recorded."""
        if c.presence_source is None:
            return None
        fix = await c.presence_source.latest_fix(
            circle_id=circle.circle_id, person_id=ref.person_id
        )
        return fix.ts if fix is not None else None

    c._been_last_recorded = _been_last_recorded
    return (False, None, None)


async def _where_been__p2(c):

    async def _been_usual_trips(subject_id: str, owner: str) -> list[dict]:
        """The journeys this person repeats, from one of their places to
        another, with how long they actually take at that time of day,
        worked out as words. FAIL-SOFT: an annotation, so a failure
        answers the routes without it."""
        try:
            stays = await c.mobility.stays_for_subject(subject_id=subject_id)
            trips = await c.mobility.trips_for_subject(subject_id=subject_id)
            places = {p.id: p for p in await c.mobility.places_for_subject(subject_id=subject_id)}
        except Exception:
            log.exception("where_been usual trips failed; routes answered without them")
            return []

        def _name(pid: int) -> str:
            p = places.get(pid)
            if p is None:
                return "a place they go"
            return c._bind_place(
                c._place_words(p.label, p.kind, p.saved_place_id is not None), p.id, owner
            )

        out = []
        for pattern in trip_patterns(stays, trips, tz_name=c._been_tz_name)[:8]:
            takes = (
                f"about {pattern.median_minutes} min"
                if pattern.usual_max_minutes - pattern.usual_min_minutes < 5
                else (f"{pattern.usual_min_minutes}-{pattern.usual_max_minutes} min")
            )
            out.append(
                {
                    "from": _name(pattern.origin_id),
                    "to": _name(pattern.destination_id),
                    ("when"): f"{pattern.day_type} {pattern.time_of_day}s",
                    "usually_takes": takes,
                }
            )
        return out

    c._been_usual_trips = _been_usual_trips

    async def _been_mark_unusual(subject_id: str, day_stays: list[dict]) -> None:
        """Each of the day's stays gains `unusual`, how it differed from
        the subject's OWN past at that place, when it did
        (`detect_stay_anomalies`, computed on read). FAIL-SOFT: the flags
        are an annotation, and losing them must never cost the day's
        answer, so any failure leaves the stays exactly as read."""
        if not day_stays:
            return
        try:
            history = await c.mobility.stays_for_subject(subject_id=subject_id)
            labels = {
                p.id: c._place_words(p.label, p.kind, p.saved_place_id is not None)
                for p in await c.mobility.places_for_subject(subject_id=subject_id)
            }
            by_start = {h.started_ts: h for h in history}
            computed: list[tuple[dict, list[dict]]] = []
            for row in day_stays:
                stay = by_start.get(row["started_ts"])
                if stay is None:
                    continue
                flags = []
                for a in detect_stay_anomalies(
                    stay,
                    history=history,
                    tz_name=c._been_tz_name,
                    min_samples=c._been_anomaly_min_samples,
                    z_threshold=c._been_anomaly_z,
                    novelty_radius_m=c._been_novelty_radius_m,
                ):
                    flag: dict = {"kind": a.kind}
                    if a.kind == "duration":
                        flag |= {
                            "minutes": round((a.observed or 0) / 60000),
                            "usual_minutes": round((a.expected or 0) / 60000),
                        }
                    elif a.kind in ("arrival", "departure"):
                        flag |= {
                            "at": c._been_clock(a.observed),
                            "usually": c._been_clock(a.expected),
                        }
                    elif a.kind == "unusual_place":
                        flag["usually_at"] = labels.get(a.expected_place_id)
                    flags.append(flag)
                if flags:
                    computed.append((row, flags))
        except Exception:
            log.exception("where_been unusual-stay flags failed; day answered without them")
            return
        for row, flags in computed:
            row["unusual"] = flags

    c._been_mark_unusual = _been_mark_unusual
    return (False, None, None)


async def _where_been__p3(c):

    def _been_ok_note(people: list[dict]) -> str:
        """How to report an `ok` (ADR 0015 §3), per arm and with the window
        this answer was read over, so a number is never quoted without it.
        The `unlabeled` and unmatched-stay clauses only when one is there:
        they carry the rule against guessing a neighbourhood from lat/lng
        to the answer it is about."""
        if c.what == "day":
            note = f"Give the date ({c.day_iso}) with anything you quote." + (
                (" Never read lat/lng aloud.") if c.content_checks else ""
            )
            if any(s.get("unusual") for p in people for s in p.get("stays", ())):
                note += (
                    " A stay with unusual differed from their own usual at that place, as "
                    "listed: say it as a comparison with their past, never as a judgement of them."
                )
            if any(s.get("place_kind") is None for p in people for s in p.get("stays", ())):
                note += (
                    " A stay with no place_kind was not matched to a place: say that plainly, "
                    "and never guess a neighbourhood from its lat/lng (reverse_geocode can name "
                    "the spot)."
                )
            return note
        if c.what == "places":
            note = (
                "These are usual places and say nothing about where anyone is now. Quote a "
                "stay count or dwell time with its span (first_seen_iso to last_seen_iso)."
            )
            if any(pl.get("kind") == "unlabeled" for p in people for pl in p.get("places", ())):
                note += (
                    " A place of kind unlabeled has no home or work pattern: never call it "
                    "their home or their work. Call a place by its `name`. A name that says "
                    "«near» a street is a place they stop at by that street, for as long as "
                    "its dwell says: not the street, and not a road they passed. Do not "
                    "invent a name."
                )
            return note
        return (
            f"This is a pattern over the past {c.window_days} days and says nothing about "
            f"where anyone is now. Quote every count with that window."
        )

    c._been_ok_note = _been_ok_note
    return (False, None, None)


async def _where_been__p4(c):

    async def _been_through(circle) -> dict:
        """`where_been`'s read for one circle, behind `_presence_audience`
        (the guard it shares with `routine_read`)."""
        asked = None if c.name is None else str(c.name).strip()
        refs, refusal = await c._presence_audience(
            circle,
            asked,
            tool="where_been",
            anyone=("where anyone has been"),
            people_phrase="where people have been",
        )
        if refusal is not None:
            return refusal
        window_end_ms = c.now_ms if c.day_end_ms is None else min(c.now_ms, c.day_end_ms)
        try:
            people: list[dict] = []
            stopped: list[dict] = []
            seen_subjects: set[str] = set()
            for ref in refs:
                subject_id = c.persons.subject_id_for(ref.person_id)
                if subject_id in seen_subjects:
                    continue
                seen_subjects.add(subject_id)
                payload = await c._been_read(subject_id, ref.name)
                last_ms = await c._been_last_recorded(circle, ref)
                edge: dict = {}
                if last_ms is not None:
                    edge["last_recorded_iso"] = c._been_iso(last_ms)
                    if window_end_ms - last_ms > STALE_AFTER_MS:
                        stopped.append({"person": ref.name, **edge})
                if payload:
                    people.append({"person": ref.name, **payload, **edge})
        except Exception:
            log.exception("where_been read failed")
            return {
                "status": "unavailable",
                "note": (
                    "I couldn't check where they've been just "
                    "now. Say you couldn't look, not that there is no pattern."
                ),
            }
        coverage: dict = {}
        if stopped:
            coverage = {
                "recording_stopped": stopped,
                "recording_note": (
                    "Location tracking "
                    "recorded nothing for these people after their last_recorded_iso. Their "
                    "history "
                    "ends there: what came after is missing, not them staying put. Say where the "
                    "record stops; do not fill it in."
                ),
            }
        if not people:
            target = asked if asked else "anyone here"
            if c.what == "day":
                target = f"{target} on {c.day_iso}"
            return {
                "status": "nothing_found",
                "note": (
                    f"I have no derived pattern for {target} "
                    f"yet; nothing is recorded in this window. Say there's nothing yet, and do not "
                    f"invent one."
                ),
                **coverage,
            }
        return {
            "status": "ok",
            "what": c.what,
            "window": {"date": c.day_iso} if c.what == "day" else {("days"): c.window_days},
            "people": people,
            **coverage,
            "note": c._been_ok_note(people),
        }

    c._been_through = _been_through
    c.results: list[dict] = []
    for _w1 in c.presence_circles:
        c.circle = _w1
        c.out = await c._been_through(c.circle)
        if str(c.out.get("status", "")) == "ok":
            _record(c.metrics, "where_been", "ok", what=c.what, people=len(c.out["people"]))
            return (True, None, c.out)
        c.results.append(c.out)
    c.answer = next((r for r in c.results if r.get("status") != "refused"), c.results[0])
    _record(c.metrics, "where_been", str(c.answer.get("status", "unavailable")))
    return (False, None, None)


async def _where_been__p5(c):
    return (True, None, c.answer)
    return (False, None, None)


async def _where_been__been_read__p1(c):
    if c.what == "places":
        c.rows = await c.mobility.places_with_aggregates(subject_id=c.subject_id)
        c.places = [
            {
                k: row[k]
                for k in (
                    "kind",
                    "saved",
                    "confidence",
                    "first_seen_ts",
                    ("last_seen_ts"),
                    "stay_count",
                    "total_dwell_ms",
                )
            }
            | {
                "first_seen_iso": c._been_iso(row["first_seen_ts"]),
                "last_seen_iso": c._been_iso(row["last_seen_ts"]),
                "name": c._bind_place(
                    c._place_words(row["label"], row["kind"], bool(row["saved"])),
                    row["id"],
                    c.owner,
                ),
            }
            for row in c.rows
        ]
        return (True, None, {"places": c.places} if c.places else {})
    if c.what == "rhythm":
        c.bins = await c.mobility.rhythm_bins(subject_id=c.subject_id, window_ms=c.window_ms)
        return (True, None, {"rhythm": c.bins} if c.bins else {})
    if c.what == "routes":
        c.out = await c.mobility.trips_with_corridors(
            subject_id=c.subject_id, window_ms=c.window_ms
        )
        c.corridors = [
            {k: __cv[k] for k in ("id", "sample_count", "first_seen_ts", "last_seen_ts")}
            | {
                ("first_seen_iso"): c._been_iso(__cv["first_seen_ts"]),
                "last_seen_iso": c._been_iso(__cv["last_seen_ts"]),
            }
            for __cv in c.out["corridors"]
        ]
        if not c.out["trips"] and (not c.corridors):
            return (True, None, {})
        c.trips = [
            {
                k: t[k]
                for k in (
                    "id",
                    "started_ts",
                    "ended_ts",
                    "distance_m",
                    "avg_speed_kmh",
                    ("fix_count"),
                    "corridor_id",
                )
            }
            | {"started_iso": c._been_iso(t["started_ts"]), "ended_iso": c._been_iso(t["ended_ts"])}
            for t in c.out["trips"]
        ]
        c.usual = await c._been_usual_trips(c.subject_id, c.owner)
        return (
            True,
            None,
            {
                "trips": c.trips,
                "corridors": c.corridors,
                **({"usual_trips": c.usual} if c.usual else {}),
            },
        )
    if c.day_start_ms is None:
        raise RuntimeError(
            "where_been: what='day' without a day window — day_start_ms is bound "
            "by the same branch that reaches here"
        )
    c.fetch_ms = c.now_ms - c.day_start_ms
    if not 0 <= c.fetch_ms <= c._been_max_days * c._been_day_ms:
        return (True, None, {})
    c.stays = await c.mobility.stays_for_subject_window(
        subject_id=c.subject_id, window_ms=c.fetch_ms
    )
    return (False, None, None)


async def _where_been__been_read__p2(c):
    c.day_stays = [
        {
            **{k: v for k, v in s.items() if k not in ("place_label", "place_saved")},
            ("place"): c._place_words(
                s.get("place_label"), s["place_kind"], bool(s.get("place_saved"))
            )
            if s.get("place_kind")
            else None,
            "started_iso": c._been_iso(s["started_ts"]),
            "ended_iso": c._been_iso(s["ended_ts"]),
        }
        for s in c.stays
        if s["started_ts"] < c.day_end_ms
    ]
    await c._been_mark_unusual(c.subject_id, c.day_stays)
    c.out = await c.mobility.trips_with_corridors(subject_id=c.subject_id, window_ms=c.fetch_ms)
    c.day_trips = [
        {k: t[k] for k in ("started_ts", "ended_ts", "distance_m", "avg_speed_kmh", ("fix_count"))}
        | {"started_iso": c._been_iso(t["started_ts"]), "ended_iso": c._been_iso(t["ended_ts"])}
        for t in c.out["trips"]
        if t["started_ts"] < c.day_end_ms
    ]
    if not c.day_stays and (not c.day_trips):
        return (True, None, {})
    return (True, None, {"stays": c.day_stays, "trips": c.day_trips})
    return (False, None, None)


async def _where_been__been_read(c, subject_id, owner):
    """One arm's read for one subject, coordinates never selected and
    corridor sequences never carried out (R-28). An empty answer means
    «no derived data», which the caller renders as `nothing_found`."""
    c.subject_id = subject_id
    c.owner = owner
    k_, _, v_ = await _where_been__been_read__p1(c)
    if k_:
        return v_
    k_, _, v_ = await _where_been__been_read__p2(c)
    if k_:
        return v_
