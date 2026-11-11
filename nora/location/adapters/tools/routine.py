from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from nora.domain.mobility_forecast import (
    forecast_hour,
)

log = logging.getLogger("nora.tools")


def _make__routine_live(deps):
    presence_source = deps.presence_source
    _routine_live_max_age_ms = deps._routine_live_max_age_ms
    _routine_match_m = deps._routine_match_m

    async def _routine_live(
        circle: Any,
        ref: Any,
        places: dict[int, Any],
        now_ms: int,
    ) -> dict | None:
        """Where the person is by their newest raw fix, as one of their places"""
        if presence_source is None:
            return None
        try:
            fix = await presence_source.latest_fix(
                circle_id=circle.circle_id,
                person_id=ref.person_id,
            )
        except Exception:
            log.exception("routine_read live read failed")
            return None
        if fix is None or now_ms - fix.ts > _routine_live_max_age_ms:
            return None
        from nora.domain.presence import haversine_distance_m as _dist

        best = None
        for place in places.values():
            d = _dist(fix.lat, fix.lng, place.lat, place.lng)
            if d <= max(place.radius_m, _routine_match_m) and (best is None or d < best[0]):
                best = (d, place)
        return {"place": best[1] if best else None, "ts": fix.ts}

    return _routine_live


def _make__routine_clock(deps):
    def _routine_clock(minute: int) -> str:
        m = minute % 1440
        return f"{m // 60:02d}:{m % 60:02d}"

    return _routine_clock


def _make__routine_span(deps):
    _routine_clock = _make__routine_clock(deps)

    def _routine_span(habit: Any) -> str:
        if habit.latest - habit.earliest < 10:
            return f"around {_routine_clock(habit.earliest)}"
        if habit.latest - habit.earliest > 180:
            # Chained 90-minute gaps can span an evening: that is no fixed
            # time, and saying «16:00-02:40» would read as one.
            return (
                f"at no fixed time, anywhere from {_routine_clock(habit.earliest)} "
                f"to {_routine_clock(habit.latest)}"
            )
        return f"{_routine_clock(habit.earliest)}-{_routine_clock(habit.latest)}"

    return _routine_span


def _make__routine_today(deps):
    _been_tz_name = deps._been_tz_name
    from nora.location.adapters.tools.routes import _make__bind_place

    _bind_place = _make__bind_place(deps)
    _place_words = deps._place_words
    _routine_clock = _make__routine_clock(deps)

    def _routine_today(
        stays: list,
        places: dict[int, Any],
        live: dict | None,
        *,
        day: Any,
        now_ms: int,
        start_ms: int,
        end_ms: int,
        owner: str,
    ) -> str | None:
        """M4: on the day itself, where they are now against where they"""
        hk_now = datetime.fromtimestamp(now_ms / 1000, tz=ZoneInfo(_been_tz_name))
        if live is None or (hk_now.date() != day and not start_ms <= now_ms < end_ms):
            return None
        here = live["place"]
        here_name = (
            _place_words(here.label, here.kind, here.saved_place_id is not None)
            if here is not None
            else "somewhere that is not one of their places"
        )
        usual = forecast_hour(
            stays,
            at_ms=now_ms,
            tz_name=_been_tz_name,
            history_before_ms=now_ms,
        )
        usual_id = usual.chances[0].place_id if usual.chances else None
        as_of = _routine_clock(hk_now.hour * 60 + hk_now.minute)
        if usual.status != "ok" or not usual.chances:
            return f"now {here_name} (as of {as_of})"
        if here is not None and usual_id == here.id:
            return f"now {here_name} (as of {as_of}), as usual at this hour"
        usual_place = places.get(usual_id) if usual_id is not None else None
        usual_name = (
            _bind_place(
                _place_words(
                    usual_place.label,
                    usual_place.kind,
                    usual_place.saved_place_id is not None,
                ),
                usual_place.id,
                owner,
            )
            if usual_place is not None
            else "somewhere that is not one of their places"
        )
        return f"now {here_name} (as of {as_of}); at this hour they are usually at {usual_name}"

    return _routine_today


def _make__routine_shares(deps):
    from nora.location.adapters.tools.routes import _make__bind_place

    _bind_place = _make__bind_place(deps)
    _place_words = deps._place_words

    def _routine_shares(chances: Any, places: dict[int, Any], owner: str) -> list[dict]:
        """The window shared out between named places, their own small"""
        raw: dict[tuple[str, str], float] = {}
        for chance in chances:
            place = places.get(chance.place_id) if chance.place_id is not None else None
            if place is None:
                key = ("somewhere that is not one of their places", "elsewhere")
            elif chance.probability < 0.05:
                key = ("other places of theirs", "other")
            else:
                # Two unnamed homes must not read as one place, within this
                # answer or across the turn: the name is what `route` takes.
                name = _bind_place(
                    _place_words(place.label, place.kind, place.saved_place_id is not None),
                    place.id,
                    owner,
                )
                key = (name, place.kind)
            raw[key] = raw.get(key, 0.0) + chance.probability * 100
        floors = {k: int(v) for k, v in raw.items()}
        short = 100 - sum(floors.values())
        for k in sorted(raw, key=lambda k: (-(raw[k] - floors[k]), k))[: max(short, 0)]:
            floors[k] += 1
        return sorted(
            (
                {"place": name, "kind": kind, "percent": pct}
                for (name, kind), pct in floors.items()
                if pct > 0
            ),
            key=lambda x: -x["percent"],
        )

    return _routine_shares


def _make__routine_window_words(deps):
    _routine_day = deps._routine_day

    def _routine_window_words(start: datetime, end: datetime) -> str:
        """The asked hours the way a person names them («Wednesday evening»),"""
        hours = (end - start).total_seconds() / 3600
        day = _routine_day[start.weekday()]
        if hours >= 16:
            return f"{day}, all day"
        if end.date() > start.date() and end.hour > 0:
            part = (
                "night"
                if start.hour >= 17
                else "afternoon into the night"
                if start.hour >= 12
                else "from the day into the night"
            )
        elif start.hour < 5:
            part = "early morning"
        elif start.hour < 12:
            part = "during the day" if end.hour >= 15 or end.date() > start.date() else "morning"
        elif start.hour < 17:
            part = (
                "afternoon and evening"
                if end.hour >= 20 or end.date() > start.date()
                else "afternoon"
            )
        elif start.hour < 21:
            part = "evening"
        else:
            part = "night"
        return f"{day} {part}" if not part.startswith("during") else f"{day}, {part}"

    return _routine_window_words


def _make__routine_sureness(deps):
    _routine_ladder = deps._routine_ladder

    def _routine_sureness(percent: int, *, thin: bool) -> int:
        level = next((lvl for floor, lvl in _routine_ladder if percent >= floor), 0)
        return max(level - 1, 0) if thin else level

    return _routine_sureness


def _make__routine_person(deps):
    _routine_days = deps._routine_days
    _routine_rough = deps._routine_rough
    _routine_sureness = _make__routine_sureness(deps)
    _routine_window_words = _make__routine_window_words(deps)

    def _routine_person(
        person: str,
        shares: list[dict],
        *,
        start: datetime,
        end: datetime,
        basis: str,
        observed: int,
        weeks: int,
        with_evidence: bool,
    ) -> dict:
        """One person's routine for the window as a friend would say it: where"""
        # Only one of their own named places can be where they usually are:
        # the pooled small places and the «not theirs» bucket never are.
        real = [x for x in shares if x["kind"] not in ("other", "elsewhere")]
        top: dict | None = real[0] if real else None
        level = _routine_sureness(
            int(top["percent"]) if top else 0,
            thin=basis in _routine_rough or observed < 50,
        )
        others = [x["place"] for x in real if x is not top and x["percent"] >= 10]
        span = _routine_window_words(start, end)
        where = top["place"] if top else None
        named_in_line: list[str] = [where] if where is not None else []
        if level == 3:
            said = f"should be at {where}"
        elif level == 2:
            said = f"probably at {where}"
        elif level == 1:
            named_in_line += others[:1]
            said = f"maybe at {where}, maybe {'at ' + others[0] if others else 'somewhere else'}"
        elif where is not None:
            named_in_line = [where, *others][:3]
            said = "hard to say; sometimes " + ", sometimes ".join(named_in_line)
        else:
            said = "hard to say"
        out = {
            "person": person,
            "routine": f"{span}: {said}",
            "usually_at": where if level >= 2 else None,
            # Other places of theirs they are at some of these hours, by name,
            # so a plan can route from them too; how often is in `routine`.
            "also": [x for x in others if x not in named_in_line],
        }
        if with_evidence:
            # Asked how sure: what the past weeks WERE, a grounded count of
            # the past, never a confidence figure (none is measured).
            ranked = [x for x in shares if x["percent"] > 0]
            if len(ranked) == 1:
                on_record = f"all of it at {ranked[0]['place']}"
            else:
                on_record = ", ".join(
                    f"about {x['percent']}% at {x['place']}" for x in ranked[:2]
                ) + ("; the rest spread out" if len(ranked) > 2 else "")
            text = (
                f"Over the last {weeks} {_routine_days[start.weekday()]} at "
                f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')}, of the time "
                f"on record: {on_record}. "
                + (
                    "Nearly all of those hours are on record."
                    if observed >= 80
                    else "Most of those hours are on record."
                    if observed >= 50
                    else "Much of those hours is not on record."
                )
            )
            if basis in _routine_rough:
                text += (
                    " These hours were rarely on record, so it was read from nearby hours or days."
                )
            out["evidence"] = text
        return out

    return _routine_person


