"""The only module that reads doctrine files (spec A.3-A.5, design/17).

Doctrine is content, not code (S1). Frontmatter uses the Agent Skills shape (S3) so a
file can be promoted to a real Strands Skill without a rewrite.

**`kind` is the field this module exists to enforce.** design/17 §2 splits the
prompt by SOURCE OF TRUTH rather than by topic: `constraint`, `identity` and
`conduct` are authored — a human decides and there is nothing to check them
against — while `capability` and `situation` are derived, and any prose copy of
those is a cache that goes stale silently. That is not a style preference. A
capability sentence in an authored file has no owner, cannot be tested, and its
falseness is invisible: `identity.md` (then `soul.md`) fenced what Nora knew to
"two things, and only these" for eight weeks after three tools shipped that made
it false, and nothing in the system could notice.

`kind` is the only part of design/12 §2's authority model that CAN be
mechanised, so it is (§6). One field feeds two consumers: the gates below read
it to refuse a file that oversteps its kind, and `_assemble` renders the same
value into the section tag so the model is told from OUTSIDE what it is looking
at, instead of each file asserting its own rank in its own prose.

The three fields that are documentation rather than gates — `not_here`,
`change_policy`, `authority_above`/`authority_below` — are required anyway, and
`authority_*` is checked for internal consistency. `not_here` is the boundary
§3 says `soul.md` lacked: had it carried a line saying capability lives
elsewhere, the claim would have looked wrong the day it was written rather than
eight weeks later. None of the three is rendered — they are for whoever opens
the file, and the prompt pays for nothing it does not use.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import re
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..domain.prompt import escape_attr
from ..obs.metrics import Metrics

log = logging.getLogger("nora.doctrine")

_NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.DOTALL)
_REQUIRED = (
    "name", "description", "order", "applies_to", "version",
    "kind", "authority_above", "authority_below", "not_here", "change_policy",
)

# design/17 §4. The first three are AUTHORED — a file may declare them. The last
# two are DERIVED and have no file at all; a file claiming one is rejected in
# `_scan`, because the whole point is that they cannot be hand-written.
AUTHORED_KINDS = ("constraint", "identity", "conduct")
DERIVED_KINDS = ("capability", "situation")
KINDS = (*AUTHORED_KINDS, *DERIVED_KINDS)

# What `authority_above`/`authority_below` may name besides another section: the
# tail of design/17 §7b's chain, which runs past doctrine and terminates at tool
# output. These have no file, so they are never checked for symmetry.
CHAIN_TERMINALS = ("capability", "situation", "message-content", "tool-output")

# Who may edit a file without asking. `voice.md` is the operator's — her
# register, her teasing rules, her language policy — and until this field
# existed its only protection was the operator saying so in conversation
# (design/17 §7b item 3).
CHANGE_POLICIES = ("operator-only", "agent-may-edit")

# A bare snake_case identifier is how a tool catalogue is written, and an
# `identity` or `conduct` file has no other reason to contain one. Structural on
# purpose: it needs no list of today's tools, so it cannot go stale, and it
# already covers six of the eight natives. `tool_names` on the store closes the
# other two (`now`, `about` — ordinary English words, only detectable as
# backticked code).
#
# `about` could have avoided this list. `about` was the other candidate
# in DEC-39 and is snake_case, so the structural detector would have caught it
# for free. It was rejected on a MEASUREMENT that outweighs the tidiness: it
# collapsed tool selection onto `lookup_person`. Recorded here because from this
# module's side the snake_case name looks strictly better, and it is not.
_SNAKE_CASE_RE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")

# design/17 §4's kind taxonomy, read through RFC 2119. A `constraint` is a MUST,
# a `conduct` line is a SHOULD, and `identity`/`voice` describe and carry
# neither — so a file whose normative keyword contradicts its kind (a hard rule
# that merely SHOULD hold, a habit written as an absolute MUST, or any normative
# keyword in a file that only describes) is a category error the loader can see.
#
# Scoped to UPPERCASE keywords on purpose, which is RFC 2119 §8's own rule:
# force lives in the capitalisation, so an ordinary sentence's lowercase "should"
# or "must" never trips this — only a deliberate normative keyword in the wrong
# file does. It does NOT require any file to USE these keywords: today's doctrine
# speaks in natural imperatives ("Never reveal…", "Don't state as fact…") and
# passes untouched. The two-word forms lead the alternation so `SHOULD NOT`
# matches ahead of `SHOULD`. `MAY`/`OPTIONAL` are omitted — permission is not a
# claim any kind's register contradicts.
_RFC2119_ABSOLUTE = ("MUST NOT", "MUST", "SHALL NOT", "SHALL", "REQUIRED")
_RFC2119_ADVISORY = ("SHOULD NOT", "SHOULD", "RECOMMENDED")
_RFC2119_RE = re.compile(r"\b(" + "|".join((*_RFC2119_ABSOLUTE, *_RFC2119_ADVISORY)) + r")\b")
# What each AUTHORED kind may not contain (derived kinds have no authored file):
# a constraint that only SHOULD holds is not a MUST; a conduct habit written as
# an absolute MUST is over-stated; identity/voice describe and carry neither.
_FORBIDDEN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "constraint": _RFC2119_ADVISORY,
    "conduct": _RFC2119_ABSOLUTE,
    "identity": (*_RFC2119_ABSOLUTE, *_RFC2119_ADVISORY),
}


class DoctrineError(Exception):
    """A doctrine problem that must stop the process (strict) or skip a file (lenient)."""


@dataclass(frozen=True)
class DoctrineSection:
    name: str
    description: str
    order: int
    applies_to: tuple[str, ...]
    version: int
    kind: str
    authority_above: tuple[str, ...]
    authority_below: tuple[str, ...]
    not_here: str
    change_policy: str
    body: str
    path: Path
    sha256: str
    metadata: dict[str, Any] = field(default_factory=dict)


def parse_section(path: Path) -> DoctrineSection:
    text = Path(path).read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise DoctrineError(f"{path.name}: missing YAML frontmatter")
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise DoctrineError(f"{path.name}: unparseable frontmatter: {exc}") from exc
    if not isinstance(meta, dict):
        raise DoctrineError(f"{path.name}: frontmatter must be a mapping")
    missing = [key for key in _REQUIRED if key not in meta]
    if missing:
        keys = ", ".join(repr(k) for k in missing)
        raise DoctrineError(f"{path.name}: missing required frontmatter key(s): {keys}")
    name = str(meta.pop("name"))
    if not _NAME_RE.match(name):
        raise DoctrineError(f"{path.name}: name {name!r} must match ^[a-z0-9-]{{1,64}}$")
    applies = meta.pop("applies_to")
    if isinstance(applies, str):
        applies = [applies]
    kind = str(meta.pop("kind"))
    if kind not in KINDS:
        raise DoctrineError(
            f"{path.name}: kind {kind!r} is not one of {', '.join(KINDS)}"
        )
    change_policy = str(meta.pop("change_policy"))
    if change_policy not in CHANGE_POLICIES:
        raise DoctrineError(
            f"{path.name}: change_policy {change_policy!r} is not one of "
            f"{', '.join(CHANGE_POLICIES)}"
        )
    not_here = str(meta.pop("not_here")).strip()
    if not not_here:
        raise DoctrineError(
            f"{path.name}: not_here is empty — say what this file must NOT contain "
            "and where that lives instead"
        )
    body = match.group(2)
    return DoctrineSection(
        name=name,
        description=str(meta.pop("description")),
        order=int(meta.pop("order")),
        applies_to=tuple(str(a) for a in applies),
        version=int(meta.pop("version")),
        kind=kind,
        authority_above=_name_list(path, "authority_above", meta.pop("authority_above")),
        authority_below=_name_list(path, "authority_below", meta.pop("authority_below")),
        not_here=not_here,
        change_policy=change_policy,
        body=body,
        path=Path(path),
        sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
        metadata=dict(meta),
    )


def _name_list(path: Path, key: str, value: Any) -> tuple[str, ...]:
    """`authority_above`/`authority_below` as a tuple of names.

    An empty list is legal and meaningful — `constraints.md` has nothing above
    it, and saying so is the point of stating authority relatively rather than
    calling yourself "not negotiable" (design/17 §7b item 1).
    """
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise DoctrineError(f"{path.name}: {key} must be a list of names")
    return tuple(str(v) for v in value)


@dataclass(frozen=True)
class Doctrine:
    """A filtered, ordered assembly of doctrine sections for one `applies_to` audience,
    per spec A.4.
    """

    sections: tuple[DoctrineSection, ...]
    text: str                      # doctrine block + runtime_context — goes to system_prompt=
    doctrine_text: str              # doctrine block only — hashed, and what LeakGuard matches
    sha256: str                    # over doctrine_text (S6, audit fix #3)
    runtime_context: str
    loaded_at: float

    def versions(self) -> dict[str, int]:
        return {s.name: s.version for s in self.sections}

    def blocks(self) -> list[dict]:
        """SystemContentBlock list for a future prompt-caching cachePoint. Unused in v2."""
        out = [
            {"text": f"{_open_tag(s)}\n{s.body}\n</section>"}
            for s in self.sections
        ]
        out.append({"text": self.runtime_context})
        return out


def _open_tag(s: DoctrineSection) -> str:
    """The section's opening tag: what it IS, never what it contains (design/17 §5b).

    `version` used to be here and is gone. It is build metadata, paid on every
    model call of every tag, and it tells her nothing — at best noise, at worst
    something to reason about. `kind` and `description` replace it: `description`
    was already in every file's frontmatter, already parsed, already written for
    exactly this job, and was being discarded.

    The value of `kind` here is not decoration. `constraints.md` used to assert
    its own authority in prose, inside the very section whose authority was in
    question; an attribute states it from outside, and it is the SAME field the
    gates in `_scan` enforce, so the label and the rule cannot disagree.

    The moment an attribute starts listing what a section CONTAINS — a
    `tools="..."` — the hand-maintained catalogue has been reinvented in a new
    location. Attributes say what a section is.
    """
    return (
        f'<section kind="{escape_attr(s.kind)}" name="{escape_attr(s.name)}"'
        f' description="{escape_attr(s.description)}">'
    )


def _assemble(sections: tuple[DoctrineSection, ...]) -> str:
    parts = ["<nora_doctrine>"]
    for s in sections:
        parts.append(_open_tag(s))
        parts.append(s.body.strip("\n"))
        parts.append("</section>")
    parts.append("</nora_doctrine>")
    return "\n".join(parts)


def check_kind_boundaries(
    section: DoctrineSection, tool_names: Iterable[str] = ()
) -> None:
    """design/17 §6: an `identity` or `conduct` file may not name a tool.

    This converts "we should remember not to write capability claims in
    `identity.md`" from a convention into a gate. It is scoped to the two kinds
    whose source of truth is the operator's taste and judgement — `constraint`
    is exempt because R-14 has to be able to say `group_transcript` and
    `tag_message` by name, and those are prompt blocks rather than tools.

    Two detectors, and the structural one is the load-bearing half: a bare
    snake_case identifier is how a catalogue is written and an authored file has
    no other reason to carry one, so it needs no list of today's tools and
    cannot rot. `tool_names` adds the tools whose names are also ordinary
    English (`now`, `about`), which are only recognisable as code when
    backticked.
    """
    if section.kind not in ("identity", "conduct"):
        return
    found = _SNAKE_CASE_RE.search(section.body)
    if found:
        raise DoctrineError(
            f"{section.path.name}: a {section.kind} file may not name a tool "
            f"({found.group(0)!r}). Capability is generated from the registry; "
            "what belongs here is who she is and how she behaves."
        )
    for tool in tool_names:
        if f"`{tool}`" in section.body:
            raise DoctrineError(
                f"{section.path.name}: a {section.kind} file may not name a tool "
                f"(`{tool}`). Capability is generated from the registry; what "
                "belongs here is who she is and how she behaves."
            )


def check_normative_register(section: DoctrineSection) -> None:
    """design/17 §4's kind taxonomy, enforced as RFC 2119 register.

    A `constraint` speaks in MUST, a `conduct` line in SHOULD, and
    `identity`/`voice` describe and use neither. This refuses the specific
    contradiction — an advisory keyword weakening a hard rule, an absolute one
    hardening a habit, or any normative keyword at all in a file that only
    describes — and nothing else. It is UPPERCASE-scoped (RFC 2119 §8), so it
    cannot be tripped by the word "should" in prose, and it requires no file to
    USE these keywords: the shipped doctrine uses natural imperatives and is
    untouched by it. The mechanical companion to `check_kind_boundaries` — kind
    is the one part of design/12 §2's authority model that can be mechanised, so
    this mechanises another edge of it.
    """
    forbidden = _FORBIDDEN_KEYWORDS.get(section.kind)
    if not forbidden:
        return
    for match in _RFC2119_RE.finditer(section.body):
        if match.group(1) in forbidden:
            raise DoctrineError(
                f"{section.path.name}: a {section.kind} file may not use the "
                f"RFC 2119 keyword {match.group(1)!r}. A constraint is a MUST, a "
                "conduct line is a SHOULD, and identity/voice describe and use "
                "neither — the keyword contradicts the file's kind."
            )


CAPABILITY_ORDER = 35
CAPABILITY_DESCRIPTION = (
    "What you can find out, and when to reach for it. Generated from the tools "
    "you were given — never written by hand, so it cannot be out of date."
)


def _capability_section(
    body: str, authored: Sequence[DoctrineSection], dir: Path
) -> DoctrineSection:
    """The one section with no file (design/17 §4, §5).

    `order = 35` puts it after `conduct` and before `constraints`, which is
    where design/17 §7b's chain puts it: constraint -> identity/voice -> conduct
    -> capability -> situation. A file claiming order 35 collides and is
    rejected by the existing duplicate-order rule, which is the behaviour we
    want — there is only one capability section and it is this one.

    `authority_above` is DERIVED — it is exactly the authored sections that
    already say `capability` is below them — so the symmetry check in
    `check_authority_chain` holds without anyone maintaining both ends, and it
    stays holding when a fifth authored file is added. Deriving it rather than
    hard-coding the four names is the difference between a declaration that
    tracks the files and one that has to be remembered.
    """
    return DoctrineSection(
        name="capability",
        description=CAPABILITY_DESCRIPTION,
        order=CAPABILITY_ORDER,
        applies_to=("always",),
        version=1,
        kind="capability",
        authority_above=tuple(s.name for s in authored if "capability" in s.authority_below),
        authority_below=("situation", "message-content", "tool-output"),
        not_here="Nothing is authored here at all — the registry is the source.",
        change_policy="agent-may-edit",
        body=body,
        path=Path(dir) / "(generated)",
        sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
    )


def check_authority_chain(sections: Sequence[DoctrineSection]) -> None:
    """`authority_above`/`authority_below` name real things, and agree with each other.

    design/17 §6 is honest that the ordering BETWEEN authored files is asserted,
    not mechanical — nothing reads the section tag at inference time. What can
    be checked is that the declaration is internally coherent: every name is
    either another section or one of the chain's terminals, nothing outranks
    itself, and if A says B is below it then B says A is above it. That is what
    stops half of a re-ordering being written and the other half forgotten,
    which is the failure mode a relative declaration exists to prevent.
    """
    by_name = {s.name: s for s in sections}
    known = set(by_name) | set(CHAIN_TERMINALS)
    for s in sections:
        for key, names in (("authority_above", s.authority_above),
                           ("authority_below", s.authority_below)):
            for other in names:
                if other == s.name:
                    raise DoctrineError(
                        f"{s.path.name}: {key} names its own section {other!r}"
                    )
                if other not in known:
                    raise DoctrineError(
                        f"{s.path.name}: {key} names {other!r}, which is neither a "
                        f"doctrine section nor one of {', '.join(CHAIN_TERMINALS)}"
                    )
        for other in s.authority_below:
            peer = by_name.get(other)
            if peer is not None and s.name not in peer.authority_above:
                raise DoctrineError(
                    f"{s.path.name}: says {other!r} is below it, but {peer.path.name} "
                    f"does not list {s.name!r} in authority_above"
                )
        for other in s.authority_above:
            peer = by_name.get(other)
            if peer is not None and s.name not in peer.authority_below:
                raise DoctrineError(
                    f"{s.path.name}: says {other!r} is above it, but {peer.path.name} "
                    f"does not list {s.name!r} in authority_below"
                )


class DoctrineStore:
    def __init__(self, dir: Path, *, strict: bool = True, reload: str = "mtime",
                 canary: str = "", display_name: str = "Nora", trigger: str = "",
                 language_hint: str = "", tool_names: Sequence[str] = (),
                 capability: str = "", metrics: Metrics) -> None:
        # `tool_names` is what the tool-catalogue gate checks BACKTICKED text
        # against; the structural half of that gate (bare snake_case) runs
        # whether or not it is supplied, so an omitted list narrows the check
        # rather than disabling it. The composition root passes the registry's
        # own names — see `nora.app.composition`.
        self._dir = Path(dir)
        self._strict = strict
        self._reload = reload
        self._canary = canary
        self._display_name = display_name
        self._trigger = trigger
        self._language_hint = language_hint
        self._tool_names = tuple(tool_names)
        # The generated `kind: capability` body (`ToolRegistry.capability()`).
        # It arrives as a STRING rather than being read off disk because that is
        # the whole point: there is no file, so there is nothing for anyone to
        # hand-edit and nothing to go stale. An empty value assembles no
        # capability section at all, which is what every test that is not about
        # capability wants.
        self._capability = capability
        self._metrics = metrics
        self._sections: tuple[DoctrineSection, ...] = ()
        self._fingerprint: tuple | None = None
        self._cache: dict[str, Doctrine] = {}
        self._last_good: dict[str, Doctrine] = {}
        self.reload_failures = 0

    def _fingerprint_dir(self) -> tuple:
        return tuple(sorted((p.name, p.stat().st_mtime_ns, p.stat().st_size)
                            for p in self._dir.glob("*.md")))

    def _scan(self) -> None:
        files = sorted(self._dir.glob("*.md"), key=lambda p: p.name)
        if not files:
            raise DoctrineError(f"no doctrine files (*.md) in {self._dir}")
        sections: list[DoctrineSection] = []
        for path in files:
            try:
                section = parse_section(path)
                if section.kind in DERIVED_KINDS:
                    raise DoctrineError(
                        f"{path.name}: kind {section.kind!r} is DERIVED and has no "
                        "file. Capability is generated from the tool registry and "
                        "situation from the running turn; an authored copy of "
                        "either is a cache with no invalidation."
                    )
                check_kind_boundaries(section, self._tool_names)
                check_normative_register(section)
                sections.append(section)
            except DoctrineError:
                if self._strict:
                    raise
                log.warning("skipping invalid doctrine file %s", path.name)
        if self._capability:
            sections.append(_capability_section(self._capability, sections, self._dir))
        by_name: dict[str, Path] = {}
        by_order: dict[int, Path] = {}
        for s in sections:
            if s.name in by_name:
                raise DoctrineError(
                    f"duplicate name {s.name!r}: {by_name[s.name].name}, {s.path.name}"
                )
            if s.order in by_order:
                raise DoctrineError(
                    f"duplicate order {s.order}: {by_order[s.order].name}, {s.path.name}"
                )
            by_name[s.name], by_order[s.order] = s.path, s.path
        check_authority_chain(sections)
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
        except DoctrineError as exc:
            self._on_reload_failure(exc)
            return
        self._fingerprint = fingerprint

    def _on_reload_failure(self, exc: Exception) -> None:
        if not self._last_good:
            raise exc if isinstance(exc, DoctrineError) else DoctrineError(str(exc))
        self.reload_failures += 1
        self._metrics.inc("nora_doctrine_reload_failures_total")
        # Advance the fingerprint so a broken file is not re-parsed on every tag.
        with contextlib.suppress(OSError):
            self._fingerprint = self._fingerprint_dir()
        self._cache = dict(self._last_good)
        log.error("doctrine_reload_failed: %s (keeping the last good doctrine)", exc)

    def _runtime_context(self) -> str:
        return (
            "<runtime_context>\n"
            f"display_name: {self._display_name}\n"
            f"trigger: {self._trigger}\n"
            f"language: {self._language_hint}\n"
            f"canary: {self._canary}\n"
            "</runtime_context>"
        )

    def get(self, *, applies_to: str = "tag") -> Doctrine:
        self._maybe_rescan()
        cached = self._cache.get(applies_to)
        if cached is not None:
            return cached
        selected = tuple(
            s for s in self._sections
            if applies_to in s.applies_to or "always" in s.applies_to
        )
        doctrine_text = _assemble(selected)
        runtime = self._runtime_context()
        doctrine = Doctrine(
            sections=selected,
            text=f"{doctrine_text}\n{runtime}",
            doctrine_text=doctrine_text,
            sha256=hashlib.sha256(doctrine_text.encode("utf-8")).hexdigest(),
            runtime_context=runtime,
            loaded_at=time.time(),
        )
        self._cache[applies_to] = doctrine
        self._last_good[applies_to] = doctrine
        return doctrine
