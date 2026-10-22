"""The shared code-rendered Character surface: prompt, portal and index."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime

from nora.domain.culture import split_culture_key
from nora.domain.memory_item import TRAIT_SLOTS, content_text
from nora.domain.persona import PERSONA_HKT, PersonaLine, format_fact, rank

PROFILE_LABELS = {"occupation": "Occupation", "residence": "Lives in", "languages": "Languages"}


def format_profile(key: str | None, value: object, *, mode: str | None = None,
                   as_of: int | None = None, valid_from: int | None = None,
                   valid_until: int | None = None,
                   name_of: Callable[[str], str] | None = None) -> str:
    if key is None:
        return ""
    text = format_fact(key, value, name_of=name_of, as_of=as_of,
                       valid_from=valid_from, valid_until=valid_until)
    if key == "attribute" and isinstance(value, dict) and value.get("name") == "workplace":
        text = text.replace("workplace:", "Works at:", 1)
    if key in PROFILE_LABELS:
        text = text.replace(key + ":", PROFILE_LABELS[key] + ":", 1)
    text = " ".join(text.split())
    return text + f" [{mode}]" if text and mode else text


def format_line(line: PersonaLine, *, name_of: Callable[[str], str] | None = None) -> str:
    item = line.memory_item
    if item.part == "identity":
        return format_profile(line.fact_key, item.content.get("fact_value"), mode=item.mode,
                              as_of=item.said_at, valid_from=line.valid_from,
                              valid_until=line.valid_until, name_of=name_of)
    # Legacy prose remains readable in history; it is not a classified card trait.
    if not item.content:
        return line.text
    text = content_text(line.content)
    if item.part == "hypothesis":
        prediction = " ".join((line.prediction or "").split())
        return f"Hypothesis [{item.mode}]: {text}; predicts: {prediction}"
    def day(value: object) -> str:
        return (datetime.fromtimestamp(value / 1000, PERSONA_HKT).date().isoformat()
                if isinstance(value, (int, float)) else "unknown")
    return (f"{item.kind.title()} [{day(item.strength.get('first'))} to "
            f"{day(item.strength.get('last'))}; {item.mode}]: {text}")


def format_card(lines: Sequence[PersonaLine], *, now_ms: int,
                name_of: Callable[[str], str] | None = None,
                location: bool = False, max_chars: int = 1200) -> str:
    """Omit whole lower-ranked lines; never cut a qualification or prediction."""
    eligible = [x for x in lines if x.state in ("current", "disputed")
                and (location or not x.location)
                and (x.valid_from is None or x.valid_from <= now_ms)
                and (x.valid_until is None or x.valid_until > now_ms)
                and (x.part != "hypothesis" or (x.prediction_window_end or 0) > now_ms)]
    eligible.sort(key=lambda x: (("identity", "character", "hypothesis").index(x.part),
                                -(rank(x, subject=split_culture_key(x.persona_key)[2]) or 0),
                                -(x.last_supported_at or x.as_of or 0), x.id))
    result: list[tuple[PersonaLine, str]] = []
    for line in eligible:
        if ((line.part != "identity" and not line.content)
                or (line.part == "character" and line.kind not in TRAIT_SLOTS)):
            continue
        text = format_line(line, name_of=name_of)
        if line.state == "disputed" and text:
            text += " (disputed)"
        if text and len("\n".join([*(t for _, t in result), text])) <= max_chars:
            result.append((line, text))
    result.sort(key=lambda entry: (("identity", "character", "hypothesis").index(entry[0].part),
                                   entry[0].kind))
    return "\n".join(text for _, text in result)
