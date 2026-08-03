"""The only module that reads persona files (spec A.3–A.5).

Persona is content, not code (S1). Frontmatter uses the Agent Skills shape (S3) so a
file can be promoted to a real Strands Skill without a rewrite.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..obs.metrics import METRICS

log = logging.getLogger("nora.persona")

_NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.DOTALL)
_REQUIRED = ("name", "description", "order", "applies_to", "version")


class PersonaError(Exception):
    """A persona problem that must stop the process (strict) or skip a file (lenient)."""


@dataclass(frozen=True)
class PersonaSection:
    name: str
    description: str
    order: int
    applies_to: tuple[str, ...]
    version: int
    body: str
    path: Path
    sha256: str
    metadata: dict[str, Any] = field(default_factory=dict)


def parse_section(path: Path) -> PersonaSection:
    text = Path(path).read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise PersonaError(f"{path.name}: missing YAML frontmatter")
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise PersonaError(f"{path.name}: unparseable frontmatter: {exc}") from exc
    if not isinstance(meta, dict):
        raise PersonaError(f"{path.name}: frontmatter must be a mapping")
    missing = [key for key in _REQUIRED if key not in meta]
    if missing:
        keys = ", ".join(repr(k) for k in missing)
        raise PersonaError(f"{path.name}: missing required frontmatter key(s): {keys}")
    name = str(meta.pop("name"))
    if not _NAME_RE.match(name):
        raise PersonaError(f"{path.name}: name {name!r} must match ^[a-z0-9-]{{1,64}}$")
    applies = meta.pop("applies_to")
    if isinstance(applies, str):
        applies = [applies]
    body = match.group(2)
    return PersonaSection(
        name=name,
        description=str(meta.pop("description")),
        order=int(meta.pop("order")),
        applies_to=tuple(str(a) for a in applies),
        version=int(meta.pop("version")),
        body=body,
        path=Path(path),
        sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
        metadata=dict(meta),
    )


@dataclass(frozen=True)
class Persona:
    """A filtered, ordered assembly of persona sections for one `applies_to` audience,
    per spec A.4.
    """

    sections: tuple[PersonaSection, ...]
    text: str                      # persona block + runtime_context — goes to system_prompt=
    persona_text: str              # persona block only — hashed, and what LeakGuard matches
    sha256: str                    # over persona_text (S6, audit fix #3)
    runtime_context: str
    loaded_at: float

    def versions(self) -> dict[str, int]:
        return {s.name: s.version for s in self.sections}

    def blocks(self) -> list[dict]:
        """SystemContentBlock list for a future prompt-caching cachePoint. Unused in v2."""
        out = [
            {"text": f'<section name="{s.name}" version="{s.version}">\n{s.body}\n</section>'}
            for s in self.sections
        ]
        out.append({"text": self.runtime_context})
        return out


def _assemble(sections: tuple[PersonaSection, ...]) -> str:
    parts = ["<nora_persona>"]
    for s in sections:
        parts.append(f'<section name="{s.name}" version="{s.version}">')
        parts.append(s.body.strip("\n"))
        parts.append("</section>")
    parts.append("</nora_persona>")
    return "\n".join(parts)


class PersonaStore:
    def __init__(self, dir: Path, *, strict: bool = True, reload: str = "mtime",
                 canary: str = "", display_name: str = "Nora", trigger: str = "",
                 language_hint: str = "", metrics=METRICS) -> None:
        self._dir = Path(dir)
        self._strict = strict
        self._reload = reload
        self._canary = canary
        self._display_name = display_name
        self._trigger = trigger
        self._language_hint = language_hint
        self._metrics = metrics
        self._sections: tuple[PersonaSection, ...] = ()
        self._fingerprint: tuple | None = None
        self._cache: dict[str, Persona] = {}
        self._last_good: dict[str, Persona] = {}
        self.reload_failures = 0

    def _fingerprint_dir(self) -> tuple:
        return tuple(sorted((p.name, p.stat().st_mtime_ns, p.stat().st_size)
                            for p in self._dir.glob("*.md")))

    def _scan(self) -> None:
        files = sorted(self._dir.glob("*.md"), key=lambda p: p.name)
        if not files:
            raise PersonaError(f"no persona files (*.md) in {self._dir}")
        sections: list[PersonaSection] = []
        for path in files:
            try:
                sections.append(parse_section(path))
            except PersonaError:
                if self._strict:
                    raise
                log.warning("skipping invalid persona file %s", path.name)
        by_name: dict[str, Path] = {}
        by_order: dict[int, Path] = {}
        for s in sections:
            if s.name in by_name:
                raise PersonaError(f"duplicate name {s.name!r}: {by_name[s.name].name}, {s.path.name}")
            if s.order in by_order:
                raise PersonaError(
                    f"duplicate order {s.order}: {by_order[s.order].name}, {s.path.name}"
                )
            by_name[s.name], by_order[s.order] = s.path, s.path
        # `order` is authoritative; filenames break ties.
        self._sections = tuple(sorted(sections, key=lambda s: (s.order, s.path.name)))
        self._cache.clear()

    def _maybe_rescan(self) -> None:
        if self._sections and self._reload == "startup":
            return
        try:
            fingerprint = self._fingerprint_dir()
        except OSError as exc:                       # directory vanished mid-run
            self._on_reload_failure(exc)
            return
        if fingerprint == self._fingerprint:
            return
        try:
            self._scan()
        except PersonaError as exc:
            self._on_reload_failure(exc)
            return
        self._fingerprint = fingerprint

    def _on_reload_failure(self, exc: Exception) -> None:
        if not self._last_good:
            raise exc if isinstance(exc, PersonaError) else PersonaError(str(exc))
        self.reload_failures += 1
        self._metrics.inc("nora_persona_reload_failures_total")
        # Advance the fingerprint so a broken file is not re-parsed on every tag.
        try:
            self._fingerprint = self._fingerprint_dir()
        except OSError:
            pass
        self._cache = dict(self._last_good)
        log.error("persona_reload_failed: %s (keeping the last good persona)", exc)

    def _runtime_context(self) -> str:
        return (
            "<runtime_context>\n"
            f"display_name: {self._display_name}\n"
            f"trigger: {self._trigger}\n"
            f"language: {self._language_hint}\n"
            f"canary: {self._canary}\n"
            "</runtime_context>"
        )

    def get(self, *, applies_to: str = "tag") -> Persona:
        self._maybe_rescan()
        cached = self._cache.get(applies_to)
        if cached is not None:
            return cached
        selected = tuple(
            s for s in self._sections
            if applies_to in s.applies_to or "always" in s.applies_to
        )
        persona_text = _assemble(selected)
        runtime = self._runtime_context()
        persona = Persona(
            sections=selected,
            text=f"{persona_text}\n{runtime}",
            persona_text=persona_text,
            sha256=hashlib.sha256(persona_text.encode("utf-8")).hexdigest(),
            runtime_context=runtime,
            loaded_at=time.time(),
        )
        self._cache[applies_to] = persona
        self._last_good[applies_to] = persona
        return persona
