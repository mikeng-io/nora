from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Literal
from zoneinfo import ZoneInfo

from nora.agent.toolkit.results import _record
from nora.domain.mobility_forecast import (
    forecast_window,
    time_forecast,
)
from nora.location.adapters.tools.routine import (
    _make__routine_clock,
    _make__routine_live,
    _make__routine_person,
    _make__routine_shares,
    _make__routine_span,
    _make__routine_today,
)

log = logging.getLogger("nora.tools")


def _make__movement_person(deps):
    def _movement_person(
        person: str,
        stays: list,
        places: dict[int, Any],
        *,
        kind: str,
        place: str | None,
        day: Any,
        from_hour: int,
        to_hour: int,
        now_ms: int,
        live: dict | None,
        with_evidence: bool = False,
    ) -> dict | str | None:
        """One person's usual arrival at (or departure from) a place, as"""
        from nora.location.adapters.tools.routes import _make__bind_place

        c = SimpleNamespace(
            person=person,
            stays=stays,
            places=places,
            kind=kind,
            place=place,
            day=day,
            from_hour=from_hour,
            to_hour=to_hour,
            now_ms=now_ms,
            live=live,
            with_evidence=with_evidence,
            _been_tz_name=deps._been_tz_name,
            _bind_place=_make__bind_place(deps),
            _bound_places=deps._bound_places,
            _place_words=deps._place_words,
            _routine_clock=_make__routine_clock(deps),
            _routine_days=deps._routine_days,
            _routine_span=_make__routine_span(deps),
        )
        k_, _, v_ = _movement_person__targets(c)
        if k_:
            return v_
        k_, _, v_ = _movement_person__evidence(c)
        if k_:
            return v_
        return None

    return _movement_person


def _make_routine_read(deps):
    async def routine_read(
        date: str,
        from_hour: int,
        to_hour: int,
        name: str | None = None,
        what: Literal["where", "arrival", "departure"] = "where",
        place: str | None = None,
        detail: Literal["answer", "evidence"] = "answer",
    ) -> dict:
        """Usual whereabouts by hour of the week. Call this when somebody asks
        where someone will be.

        Never for right now (`where_is`) or the past (`where_been`).

        Returns `status`: ok, nothing_found, unavailable, refused, not_found or
        ambiguous, with a `note` on how to report it.

        Args:
            date: The local day they mean, "YYYY-MM-DD".
            from_hour: Local hour the time window starts, 0 to 23.
            to_hour: Local hour it ends, 0 to 23. At or before from_hour it
                runs past midnight.
            name: Who they asked about, exactly as they said it. Leave it out
                for the people in THIS conversation (or pass "everyone").
            what: "where" they usually are (default), or when they usually
                make their "arrival" at, or "departure" from, a place.
            place: For arrival or departure: the place, by its name, or
                "home" or "work". Leave it out for their home.
            detail: "answer" (default), or "evidence" when they ask how sure
                you are or why: what their past weeks actually were.
        """
        from nora.location.adapters.tools.presence import _make__been_iso, _make__presence_audience

        c = SimpleNamespace(
            date=date,
            from_hour=from_hour,
            to_hour=to_hour,
            name=name,
            what=what,
            place=place,
            detail=detail,
            clock=deps.clock,
            metrics=deps.metrics,
            mobility=deps.mobility,
            persons=deps.persons,
            presence_circles=deps.presence_circles,
            _been_iso=_make__been_iso(deps),
            _been_tz_name=deps._been_tz_name,
            _movement_person=_make__movement_person(deps),
            _presence_audience=_make__presence_audience(deps),
            _routine_live=_make__routine_live(deps),
            _routine_person=_make__routine_person(deps),
            _routine_shares=_make__routine_shares(deps),
            _routine_today=_make__routine_today(deps),
        )
        k_, _, v_ = await _routine_read__wired(c)
        if k_:
            return v_
        for _lt1 in c.presence_circles:
            c.circle = _lt1
            k_, f_, v_ = await _routine_read__gather(c)
            if k_:
                return v_
            if f_ == "break":
                break
            if f_ == "continue":
                continue
            k_, f_, v_ = await _routine_read__ok_note(c)
            if k_:
                return v_
            if f_ == "break":
                break
            if f_ == "continue":
                continue
        k_, _, v_ = await _routine_read__answer(c)
        if k_:
            return v_
        return None  # type: ignore[return-value]

    return routine_read


def _movement_person__targets(c):
    c.wanted = (c.place or "home").strip().casefold()
    c.bound_words = c._bound_places.get(c.wanted)
    c.targets = (
        {c.bound_words[0]}
        if c.bound_words is not None and c.bound_words[0] in c.places
        else {pid for pid, p in c.places.items() if (p.label or "").casefold() == c.wanted}
    ) or {pid for pid, p in c.places.items() if p.kind == c.wanted}
    if not c.targets:
        return (True, None, "no_place")
    if len(c.targets) == 1:
        c.only = c.places[next(iter(c.targets))]
        c.named = c._place_words(c.only.label, c.only.kind, c.only.saved_place_id is not None)
    else:
        c.named = {"home": "one of their homes", "work": "one of their workplaces"}.get(
            c.wanted, f"one of their places called {c.wanted}"
        )
    c.at_place_now = (
        None if c.live is None else c.live["place"] is not None and c.live["place"].id in c.targets
    )
    c.fc = time_forecast(
        c.stays,
        place_ids=c.targets,
        kind=c.kind,
        day=c.day,
        from_hour=c.from_hour,
        to_hour=c.to_hour,
        tz_name=c._been_tz_name,
        history_before_ms=c.now_ms,
        now_ms=c.now_ms,
        at_place_now=c.at_place_now,
    )
    if c.fc.status != "ok":
        return (True, None, None)
    c.refs: dict = {}
    if len(c.targets) == 1:
        c.named = c._bind_place(c.named, next(iter(c.targets)), c.person)
    else:
        c.refs = {
            "places": [
                c._bind_place(
                    c._place_words(
                        c.places[pid].label,
                        c.places[pid].kind,
                        c.places[pid].saved_place_id is not None,
                    ),
                    pid,
                    c.person,
                )
                for pid in sorted(c.targets, key=lambda i: (c.places[i].label or "", i))
            ]
        }
    c.arrival = c.kind == "arrival"
    c.nights = c.from_hour >= 18 or c.to_hour <= c.from_hour
    c.unit = "nights" if c.nights else "days"
    c.some = f"some {c.unit}"
    c.span_days = {
        "weekday": f"{c._routine_days[c.day.weekday()]}",
        "day_type": "weekends" if c.day.weekday() >= 5 else "weekdays",
        "all_days": "any day",
    }[c.fc.basis]

    def _when(h: Any) -> str:
        return c._routine_span(h)

    c._when = _when

    def _hedged(habits: tuple, total: int, *, thin: bool) -> str:
        """The usual time as a friend says it: «should … around 19:20»,"""
        act = f"gets to {c.named}" if c.arrival else f"leaves {c.named}"
        if not habits or not total:
            return f"{act} at no fixed time"
        main = habits[0]
        share = main.days / total
        missing = total - sum(h.days for h in habits)
        second = habits[1] if len(habits) > 1 and habits[1].days / total >= 0.2 else None
        if missing / total > 0.5:
            return f"most {c.unit} not in these hours; on the others, {act} {c._when(main)}"
        if share < 0.4 or main.latest - main.earliest > 180:
            times = [c._when(h) for h in habits[:2]]
            said = f"{act} at no fixed time; sometimes {', sometimes '.join(times)}"
        else:
            if share >= 0.75 and (not thin):
                verb = f"should get to {c.named}" if c.arrival else f"should leave {c.named}"
            elif share >= 0.5 or (share >= 0.4 and (not thin)):
                verb = f"probably gets to {c.named}" if c.arrival else f"probably leaves {c.named}"
            else:
                verb = f"maybe gets to {c.named}" if c.arrival else f"maybe leaves {c.named}"
            said = f"{verb} {c._when(main)}"
            if second is not None:
                later = second.earliest > main.latest
                said += f", though {c.some} {('not till ' if later else '')}{c._when(second)}"
        if missing / total >= 0.2:
            said += f", and {c.some} not in these hours"
        return said

    c._hedged = _hedged
    c.line = f"{c.span_days}: " + c._hedged(c.fc.habits, c.fc.days, thin=c.fc.basis != "weekday")
    c.out: dict = {
        "person": c.person,
        "what": c.kind,
        "place": c.named,
        **c.refs,
        "routine": c.line,
    }
    return (False, None, None)


def _movement_person__evidence(c):
    if c.with_evidence:
        c.counted = [f"{c._routine_span(h)} on {h.days}" for h in c.fc.habits]
        c.missing_days = c.fc.days - sum(h.days for h in c.fc.habits)
        if c.missing_days:
            c.counted.append(
                f"{('not at all' if c.arrival else 'not')} in these hours on {c.missing_days}"
            )
        c.days_word = {"weekday": c.span_days, "day_type": c.span_days, "all_days": "days"}[
            c.fc.basis
        ]
        c.out["evidence"] = (
            f"Of the last {c.fc.days} {c.days_word} they were seen: "
            f"{('got there' if c.arrival else 'left')} " + "; ".join(c.counted) + "."
        )
    c.now_local = datetime.fromtimestamp(c.now_ms / 1000, tz=ZoneInfo(c._been_tz_name))
    c.as_of = f"as of {c.now_local.strftime('%H:%M')}"
    if c.fc.today == "done":
        c.out["today"] = (
            f"already at {c.named}"
            + (
                f" since {c._routine_clock(c.fc.done_minute)}"
                if c.fc.done_minute is not None
                else ""
            )
            + f" ({c.as_of})"
            if c.arrival
            else f"already left {c.named}"
            + (f" at {c._routine_clock(c.fc.done_minute)}" if c.fc.done_minute is not None else "")
            + f" ({c.as_of})"
        )
    elif c.fc.today == "stepped_out":
        c.out["today"] = (
            f"out of {c.named} since {c._routine_clock(c.fc.done_minute or 0)} ({c.as_of}), not "
            f"long enough to say they have left for the day"
        )
    elif c.fc.today == "absent":
        c.out["today"] = (
            f"not at {c.named} now, and not there earlier in these hours today ({c.as_of})"
        )
    elif c.fc.today == "pending":
        c.state = f"not at {c.named} yet" if c.arrival else f"still at {c.named}"
        c.like = c.unit
        if not c.fc.like_today_days:
            c.out["today"] = f"{c.state} ({c.as_of}), later than usual"
        elif not c.fc.like_today:
            c.out["today"] = (
                f"{c.state} ({c.as_of}); on {c.like} like this they did not "
                f"{('get there' if c.arrival else 'leave')} in these hours"
            )
        else:
            c.main = c.fc.like_today[0]
            c.share = c.main.days / c.fc.like_today_days
            c.hedge = "probably" if c.share >= 0.6 else "maybe"
            c.tail = ""
            if c.fc.like_today_without / c.fc.like_today_days >= 0.3:
                c.tail = ", or not till after these hours"
            c.out["today"] = (
                f"{c.state} ({c.as_of}); on {c.like} like this, {c.hedge} "
                f"{('there' if c.arrival else 'gone')} {c._when(c.main)}{c.tail}"
            )
    return (True, None, c.out)
    return (False, None, None)


async def _routine_read__wired(c):
    if c.mobility is None or not c.presence_circles:
        _record(c.metrics, "routine_read", "unavailable")
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": "I can't check anyone's routine right now. Say you have no way to see it, "
                "and do not guess.",
            },
        )
    c.hk = ZoneInfo(c._been_tz_name)
    try:
        c.day = datetime.strptime(str(c.date).strip(), "%Y-%m-%d").date()
        c.start_hour, c.end_hour = (int(c.from_hour), int(c.to_hour))
        if not (0 <= c.start_hour <= 23 and 0 <= c.end_hour <= 23):
            raise ValueError("hour out of range")
        if str(c.what) not in ("where", "arrival", "departure"):
            raise ValueError("what")
        if str(c.detail) not in ("answer", "evidence"):
            raise ValueError("detail")
        c.now_ms = int((c.clock() if c.clock is not None else datetime.now(UTC)).timestamp() * 1000)
    except (TypeError, ValueError):
        _record(c.metrics, "routine_read", "unavailable", reason="window")
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": "I couldn't read that day or those hours. Give the day as YYYY-MM-DD and "
                "the hours as 0 to 23.",
            },
        )
    c.start = datetime(c.day.year, c.day.month, c.day.day, c.start_hour, tzinfo=c.hk)
    c.end_day = c.day if c.end_hour > c.start_hour else c.day + timedelta(days=1)
    c.end = datetime(c.end_day.year, c.end_day.month, c.end_day.day, c.end_hour, tzinfo=c.hk)
    c.start_ms = int(c.start.timestamp() * 1000)
    c.end_ms = int(c.end.timestamp() * 1000)
    c.window = {"from_iso": c._been_iso(c.start_ms), "to_iso": c._been_iso(c.end_ms)}
    c.asked = None if c.name is None else str(c.name).strip()
    c.results: list[dict] = []
    return (False, None, None)


async def _routine_read__gather(c):
    c.refs, c.refusal = await c._presence_audience(
        c.circle,
        c.asked,
        tool="routine_read",
        anyone="where anyone usually is",
        people_phrase="where people usually are",
    )
    if c.refusal is not None:
        c.results.append(c.refusal)
        return (False, "continue", None)
    try:
        c.people: list[dict] = []
        c.no_place = 0
        c.seen_subjects: set[str] = set()
        for _w1 in c.refs:
            c.ref = _w1
            c.subject_id = c.persons.subject_id_for(c.ref.person_id)
            if c.subject_id in c.seen_subjects:
                continue
            c.seen_subjects.add(c.subject_id)
            c.stays = await c.mobility.stays_for_subject(subject_id=c.subject_id)
            c.places = {
                p.id: p for p in await c.mobility.places_for_subject(subject_id=c.subject_id)
            }
            c.live = await c._routine_live(c.circle, c.ref, c.places, c.now_ms)
            if c.what != "where":
                c.moved = c._movement_person(
                    c.ref.name,
                    c.stays,
                    c.places,
                    kind=c.what,
                    place=c.place,
                    day=c.day,
                    from_hour=c.start_hour,
                    to_hour=c.end_hour,
                    now_ms=c.now_ms,
                    live=c.live,
                    with_evidence=c.detail == "evidence",
                )
                if c.moved == "no_place":
                    c.no_place += 1
                elif isinstance(c.moved, dict):
                    c.people.append(c.moved)
                continue
            c.forecast = forecast_window(
                c.stays,
                start_ms=c.start_ms,
                end_ms=c.end_ms,
                tz_name=c._been_tz_name,
                history_before_ms=c.now_ms,
            )
            if c.forecast.status != "ok":
                continue
            c.shares = c._routine_shares(c.forecast.chances, c.places, c.ref.name)
            c.person_out = c._routine_person(
                c.ref.name,
                c.shares,
                start=c.start,
                end=c.end,
                basis=c.forecast.basis,
                observed=round(c.forecast.observed_share * 100),
                weeks=c.forecast.support_weeks,
                with_evidence=c.detail == "evidence",
            )
            c.today_line = c._routine_today(
                c.stays,
                c.places,
                c.live,
                day=c.day,
                now_ms=c.now_ms,
                start_ms=c.start_ms,
                end_ms=c.end_ms,
                owner=c.ref.name,
            )
            if c.today_line is not None:
                c.person_out["today"] = c.today_line
            c.people.append(c.person_out)
    except Exception:
        log.exception("routine_read read failed")
        c.results.append(
            {
                "status": "unavailable",
                "note": "I couldn't check their routine just now. Say you couldn't look, not that "
                "they have no routine.",
            }
        )
        return (False, "continue", None)
    if not c.people and c.no_place and (c.no_place == len(c.seen_subjects)):
        c.results.append(
            {
                "status": "nothing_found",
                "reason": "no_place",
                "note": "I don't know a place of theirs by that name. Say so, and do not guess "
                "where it is.",
                "window": c.window,
            }
        )
        return (False, "continue", None)
    if not c.people:
        c.results.append(
            {
                "status": "nothing_found",
                "note": "There isn't enough of their history to say where they usually are then. "
                "Say you can't tell yet, and do not guess.",
                "window": c.window,
            }
        )
        return (False, "continue", None)
    return (False, None, None)


async def _routine_read__ok_note(c):
    c.ok_note = (
        "Each routine is how this person usually is at these hours, worked out from their own "
        "past weeks and already said the way a friend would put it: «should be», «probably» "
        "and «maybe» carry how sure it is."
        + (
            " `evidence` is what their past days actually were, given because someone asked how "
            "sure: counts of the past, not a confidence figure, which there is none of."
            if c.detail == "evidence"
            else ""
        )
    )
    _record(c.metrics, "routine_read", "ok", people=len(c.people))
    return (True, None, {"status": "ok", "window": c.window, "people": c.people, "note": c.ok_note})
    return (False, None, None)


async def _routine_read__answer(c):
    c.answer = next((r for r in c.results if r.get("status") != "refused"), c.results[0])
    _record(c.metrics, "routine_read", str(c.answer["status"]))
    if c.answer["status"] == "ambiguous":
        return (
            True,
            None,
            {
                "status": "ambiguous",
                "note": "More than one person here goes by that. Ask which one.",
                "could_be": c.answer["could_be"],
            },
        )
    if c.answer["status"] == "nothing_found" and c.answer.get("reason") == "no_place":
        return (
            True,
            None,
            {
                "status": "nothing_found",
                "note": "I don't know a place of theirs by that name. Say so, and do not guess "
                "where it is.",
                "window": c.answer["window"],
            },
        )
    if c.answer["status"] == "nothing_found" and "window" in c.answer:
        return (
            True,
            None,
            {
                "status": "nothing_found",
                "note": "There isn't enough of their history to say where they usually are then. "
                "Say you can't tell yet, and do not guess.",
                "window": c.answer["window"],
            },
        )
    return (True, None, {"status": c.answer["status"], "note": c.answer["note"]})
    return (False, None, None)
