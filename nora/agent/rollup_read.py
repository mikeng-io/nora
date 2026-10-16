"""What `rollup_read` does with a Rollup read, apart from the read itself:
the interval a model
writes, the lineage a room's rows must pass, and the compact typed row a model
is shown. Pure functions over `nora.domain.rollup_read`, so each rule is a
test that needs no store and no Strands.

**Lineage is applied after the read, per row** (a Rollup row's lineage is per
row): every entry of the row's set must pass for the calling room, and
a row with no lineage is unknown, which passes nowhere (`origin=None`). The
count of what did not pass is returned so the tool can say a room was not
shown some rows without saying which.

**A row is shown with its key.** `subject`, `from` and `to` are the citation
`(kind, subject, interval)` a note cites the row by: the subject is
exactly `nora.domain.rollup.Citation`'s (a person for a person-scoped kind,
`unit|key` for a unit kind), never a row id, because a rebuild recomputes rows.
Names are for reading; the `subject` is for citing.

**No coordinate** (R-28): a place is words, and an unlabelled one is said to be
unlabelled, never guessed.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Any

from nora.agent.lineage import section_permits
from nora.domain.lineage import ROW, RenderRoom
from nora.domain.rollup import iso
from nora.domain.rollup_read import RollupReadRow

__all__ = ["iso", "parse_interval", "render_row", "visible_rows"]

_HKT = timezone(timedelta(hours=8), "HKT")
_BARE_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_MS_DAY = 86_400_000


def _instant(text: str) -> tuple[int, bool]:
    """`(epoch ms, was_a_bare_date)`. A date is its Hong Kong midnight; a time
    with no offset is Hong Kong time."""
    text = text.strip()
    if _BARE_DATE.fullmatch(text):
        d = date.fromisoformat(text)
        return int(datetime(d.year, d.month, d.day, tzinfo=_HKT).timestamp() * 1000), True
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_HKT)
    return int(moment.timestamp() * 1000), False


def parse_interval(text: str) -> tuple[int, int]:
    """`[start_ms, end_ms)` of an ISO 8601 interval: `2026-09-01` (that day),
    or `start/end`, each a date or a date and time. A bare END date includes
    that whole day; a lone date-time is that instant. Raises `ValueError` on
    anything else, so a malformed interval is never read as «everything»."""
    parts = str(text).split("/")
    if len(parts) not in (1, 2) or not all(p.strip() for p in parts):
        raise ValueError(text)
    try:
        start, start_bare = _instant(parts[0])
        if len(parts) == 1:
            end = start + (_MS_DAY if start_bare else 1)
        else:
            end, end_bare = _instant(parts[1])
            end += _MS_DAY if end_bare else 0
    except (OverflowError, OSError) as exc:
        raise ValueError(text) from exc
    if end <= start:
        raise ValueError(text)
    return start, end


def visible_rows(
    rows: Sequence[RollupReadRow], target: RenderRoom,
) -> tuple[list[RollupReadRow], int]:
    """The rows whose lineage passes for `target`, and how many did not."""
    shown = [
        r for r in rows
        if section_permits(r.lineage.section(ROW), target, origin=None)
    ]
    return shown, len(rows) - len(shown)


def _place(kind: str | None, label: str | None, saved: bool) -> str:
    """What a place is called: its household name when saved; otherwise what it
    is to them near the street its label gives; `unlabeled` is an answer."""
    if label and saved:
        return label
    what = {"home": "home", "work": "work"}.get(kind or "", "an unlabelled place")
    return f"{what} near {label}" if label else what


def _rooms_by_label(
    counts: Mapping[str, int], room_label: Callable[[str], str],
) -> dict[str, int]:
    """Message counts by room LABEL. Two rooms can share a label (two «Family»
    groups; every room this one may not name is «another room»), and a dict
    keyed on the label would silently drop one count. A repeat gets a short
    ordinal, in key order so the same row renders the same way twice."""
    out: dict[str, int] = {}
    for key, n in sorted(counts.items()):
        label = room_label(key)
        if label in out:
            i = 2
            while f"{label} ({i})" in out:
                i += 1
            label = f"{label} ({i})"
        out[label] = n
    return out


def render_row(
    row: RollupReadRow, *, name_of: Callable[[str], str],
    room_label: Callable[[str], str],
) -> dict[str, Any]:
    """One row as the model reads it: its citation key first, then typed
    fields. `name_of` names a subject id (never the id); `room_label` a room
    key (never a raw conversation id)."""
    f = row.fields
    out: dict[str, Any] = {
        "kind": row.kind, "subject": row.subject,
        "from": iso(row.valid_from_ms), "to": iso(row.valid_to_ms),
    }
    minutes = round((row.valid_to_ms - row.valid_from_ms) / 60_000)
    if row.kind == "stay":
        out |= {
            "who": name_of(row.subject), "mins": minutes,
            "place": _place(f["place_kind"], f["place_label"], f["place_saved"]),
        }
    elif row.kind == "trip":
        out |= {
            "who": name_of(row.subject), "mins": minutes,
            "km": round(f["distance_m"] / 1000, 1), "kmh": round(f["avg_speed_kmh"]),
        }
    elif row.kind in ("colocation", "outing"):
        out |= {
            "who": [name_of(p) for p in f["persons"]],
            "place": _place(None, f["place_label"], f["place_saved"]),
            "open": f["open"],
        }
        if row.kind == "colocation":
            if f["at_home_of"]:
                out["at_home_of"] = [name_of(p) for p in f["at_home_of"]]
            if f["homes_unknown"]:
                out["home_unknown_for"] = [name_of(p) for p in f["homes_unknown"]]
    elif row.kind == "daybook":
        out |= {
            "who": name_of(f["person"]), "day": f["local_date"],
            "stay_mins": round(f["stay_ms"] / 60_000),
            "home_mins": round(f["home_ms"] / 60_000),
            "together_mins": round(f["colocation_ms"] / 60_000),
            "outings": f["outings"], "messages": f["messages"], "reactions": f["reactions"],
            "rooms": _rooms_by_label(f["rooms"], room_label),
        }
        if f["with_persons"]:
            out["with"] = [name_of(p) for p in f["with_persons"]]
    else:
        pair = {k: dict(zip(("a_to_b", "b_to_a"), f[k], strict=True))
                for k in ("replies", "mentions", "reactions")}
        out |= {
            "who": [name_of(p) for p in f["persons"]], "week": f["iso_week"], **pair,
            "together_mins": round(f["colocation_ms"] / 60_000),
        }
    return out
