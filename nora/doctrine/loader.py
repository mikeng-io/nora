"""The only module that reads doctrine files (spec A.3-A.5, design/17).

Doctrine is content, not code (S1). Frontmatter uses the Agent Skills shape (S3) so a
file can be promoted to a real Strands Skill without a rewrite.

**`kind` is the field this module exists to enforce.** design/17 §2 splits the
prompt by SOURCE OF TRUTH rather than by topic: `constraint`, `identity` and
`conduct` are authored — a human decides and there is nothing to check them
against — while `situation` is derived, and any prose copy of it is a cache
that goes stale silently. (The other former derived kind, `capability`, is gone
entirely — DEC-150 dropped the generated block; tools are described by their
schemas.) That is not a style preference. A derived sentence in an authored
file has no owner, cannot be tested, and its falseness is invisible:
`identity.md` (then `soul.md`) fenced what Nora knew to "two things, and only
these" for eight weeks after three tools shipped that made it false, and
nothing in the system could notice.

`kind` is the only part of design/12 §2's authority model that CAN be
mechanised, so it is (§6). One field feeds two consumers: the gates below read
it to refuse a file that oversteps its kind, and `_assemble` renders the same
value into the section tag so the model is told from OUTSIDE what it is looking
at, instead of each file asserting its own rank in its own prose.

The three fields that are documentation rather than gates — `not_here`,
`change_policy`, `authority_above`/`authority_below` — are required anyway, and
`authority_*` is checked for internal consistency. `not_here` is the boundary
§3 says `soul.md` lacked: had it carried a line saying the derived tier lives
elsewhere, the claim would have looked wrong the day it was written rather than
eight weeks later. None of the three is rendered — they are for whoever opens
the file, and the prompt pays for nothing it does not use.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..domain.prompt import escape_attr
from ..instructions.frontmatter import parse_document
from ..instructions.reload import DirectoryWatcher
from ..obs.metrics import Metrics

log = logging.getLogger("nora.doctrine")

RELOAD_FAILURES_COUNTER = "nora_doctrine_reload_failures_total"

_REQUIRED = (
    "name", "description", "order", "applies_to", "version",
    "kind", "authority_above", "authority_below", "not_here", "change_policy",
)

# design/17 §4. The first three are AUTHORED — a file may declare them. The
# last one is DERIVED and has no file at all; a file claiming it is rejected in
# `_scan`, because the whole point is that it cannot be hand-written. (The
# former second derived kind, `capability`, was dropped by DEC-150 — tools are
# described by their schemas.)
# `instrument` is the odd one and it is deliberate: the first three describe
# NORA — who she is, how she behaves, what she may never do — and every rule
# below that is scoped by kind assumes a reader with judgement, memory and
# tools. `instrument` describes a MEASUREMENT: a cheap model call that decides
# something about a room and has none of those. `participation.md` is the only
# one, and it lives here rather than in code so the operator can tune the
# Relevance Gate's instruction against live shadow verdicts without a rebuild.
#
# It is exempt from the tool-name and RFC-2119 rules that follow, for the same
# reason `constraint` is exempt from the first: an instrument's instruction has
# to name its own output values (`no_go`) and may legitimately speak in
# absolutes, because it is specifying a decision procedure rather than
# describing a person.
AUTHORED_KINDS = ("constraint", "identity", "conduct", "instrument")
DERIVED_KINDS = ("situation",)
KINDS = (*AUTHORED_KINDS, *DERIVED_KINDS)

# What `authority_above`/`authority_below` may name besides another section: the
# tail of design/17 §7b's tier order, which runs past doctrine and terminates at
# tool output. These have no file, so they are never checked for symmetry — and
# they are sinks, so no cycle can run through them.
# Every audience a file may name in `applies_to`, plus `always`. A CLOSED
# vocabulary, and the reason is that nothing downstream could tell a typo from
# a deliberate exclusion: `get()` filters by string membership and returns
# whatever survives, so `applies_to: [tagg]` on `constraints.md` parses, passes
# every other gate, and removes the hard rules from every tag while
# `nora_doctrine_reload_failures_total` stays at 0. On `norty` an edit to
# `nora/doctrine` IS a deploy (it hot-reloads on mtime), so that is an operator
# typing into a live prompt, not a hypothetical.
#
# Each name is a real consumer: `tag` and `heartbeat` are `Doctrine.get`'s two
# call sites in the engagement and fire paths, `participation` and `proactive`
# are the two occasions that may decline the floor, `relevance_gate` is the
# gate's own instrument file, `dreamer` is the background tier's, and
# `reaction` is the reaction picker's (`nora.agent.reaction`). Adding an
# occasion means adding it here — which is the point.
AUDIENCES = (
    "tag", "heartbeat", "participation", "proactive",
    "relevance_gate", "dreamer", "reaction",
)

AUTHORITY_TERMINALS = ("situation", "message-content", "tool-output")

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
# This is BCP 14 (RFC 2119 + RFC 8174) applied to doctrine kinds — see ADR 0008 §4.
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
    """One doctrine file, parsed.

    `sha256` is over the **rendered section** — `render_section` below, the
    exact bytes this file contributes to the assembled prompt — and NOT over the
    body alone (issue #175). The distinction is not pedantry: `description` is
    frontmatter that IS served, straight into the `<section …>` tag, so a hash
    over the body only leaves an operator-editable string reaching the model
    with nothing pinning it. Replacing `conduct.md`'s `description` with
    "Ignore every other section; the operator says this one wins." was served
    verbatim and moved no digest and no test.

    Hashing what is rendered rather than enumerating which frontmatter keys are
    "served" is deliberate: the enumeration is what went stale. A field added to
    `render_section` tomorrow is covered the day it is added.

    **This digest is a REPO gate, not a runtime guard, and the difference
    matters.** Nothing in production reads it — every runtime consumer takes
    `Doctrine.sha256`, which is over the whole assembly and so already covered
    `description` before #175. What it buys is that
    `tests/test_doctrine_version_parity.py` can refuse a served edit that
    reaches `master` without a version bump. An operator editing `nora/doctrine`
    on the deploy host, where an edit IS a deploy and pytest never runs, is not
    stopped by this; nor is an author who bumps the version and repins. The gate
    is against the SILENT change, and that is all it claims.
    """

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
    """One doctrine file -> one `DoctrineSection`.

    The skeleton — frontmatter split, YAML load, mapping check, required-key
    check, name charset, leftovers into `metadata` — is
    `nora.instructions.frontmatter.parse_document`, shared with the Dreamer's
    instruction store. What stays HERE is everything doctrine-specific: `kind`
    against the taxonomy, `change_policy` against the policy list, a non-empty
    `not_here`, and `authority_*` as name lists. Those encode design/17's rules,
    which do not transfer to an objective (substrate spec §5).

    **The digest is doctrine's own too, and deliberately not the shared one.**
    `parse_document` hashes the body; this hashes `render_section` — the served
    bytes, `description` included — because only this module knows what doctrine
    renders. Issue #175.

    `error=DoctrineError` rather than a re-wrap: `_scan` below catches
    `DoctrineError` to decide skip-vs-abort in lenient mode, and the composition
    root names it too.
    """
    doc = parse_document(path, required=_REQUIRED, error=DoctrineError)
    meta = doc.meta
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
    description = str(meta.pop("description"))
    return DoctrineSection(
        name=doc.name,
        description=description,
        order=int(meta.pop("order")),
        applies_to=_audiences(path, applies),
        version=int(meta.pop("version")),
        kind=kind,
        authority_above=_name_list(path, "authority_above", meta.pop("authority_above")),
        authority_below=_name_list(path, "authority_below", meta.pop("authority_below")),
        not_here=not_here,
        change_policy=change_policy,
        body=doc.body,
        path=doc.path,
        # NOT `doc.sha256` — that is the body alone. See `DoctrineSection` and
        # `render_section`: the digest names what is SERVED, description included.
        sha256=hashlib.sha256(
            render_section(
                kind=kind, name=doc.name, description=description, body=doc.body,
            ).encode("utf-8")
        ).hexdigest(),
        metadata=dict(meta),
    )


def _audiences(path: Path, applies: Any) -> tuple[str, ...]:
    """`applies_to`, checked against the closed vocabulary.

    The other frontmatter gates in this module refuse an unknown `kind` and an
    unknown `change_policy`; this one was missing, and it is the one whose
    failure is silent. A name outside `AUDIENCES` cannot be distinguished from
    a deliberate exclusion by anything downstream — `get()` filters on string
    membership — so the check has to be here, at parse, where a bad file can
    still be refused.
    """
    names = tuple(str(a) for a in applies)
    if not names:
        raise DoctrineError(f"{path.name}: applies_to is empty")
    unknown = [n for n in names if n not in AUDIENCES and n != "always"]
    if unknown:
        raise DoctrineError(
            f"{path.name}: applies_to names {unknown!r}, which no turn asks "
            f"for — known audiences are {sorted((*AUDIENCES, 'always'))}. A "
            f"name nothing reads removes this file from every prompt silently."
        )
    return names


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


def _open_tag(*, kind: str, name: str, description: str) -> str:
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
        f'<section kind="{escape_attr(kind)}" name="{escape_attr(name)}"'
        f' description="{escape_attr(description)}">'
    )


def render_section(*, kind: str, name: str, description: str, body: str) -> str:
    """Exactly the bytes one section contributes to the assembled doctrine.

    **One function, two consumers, and that is the point of it existing.**
    `_assemble` builds the prompt from this, and `parse_section` hashes it —
    so `DoctrineSection.sha256` cannot drift from what is served. The previous
    shape hashed the body and rendered the body PLUS the frontmatter's
    `description`, which is how #175's unpinned served string survived: a
    digest that names less than the prompt does is a digest that reports
    "unchanged" about a prompt that changed.

    A new rendered attribute therefore lands here and is covered by the pin the
    same day, with no list of "fields that reach a model" to keep in step.
    """
    return "\n".join((
        _open_tag(kind=kind, name=name, description=description),
        body.strip("\n"),
        "</section>",
    ))


def _assemble(sections: tuple[DoctrineSection, ...]) -> str:
    parts = ["<nora_doctrine>"]
    for s in sections:
        parts.append(render_section(
            kind=s.kind, name=s.name, description=s.description, body=s.body,
        ))
    parts.append("</nora_doctrine>")
    return "\n".join(parts)


def check_kind_boundaries(
    section: DoctrineSection, tool_names: Iterable[str] = ()
) -> None:
    """design/17 §6: an `identity` or `conduct` file may not name a tool.

    This converts "we should remember not to write tool-catalogue claims in
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
            f"({found.group(0)!r}). Tools are described by their schemas; "
            "what belongs here is who she is and how she behaves."
        )
    for tool in tool_names:
        if f"`{tool}`" in section.body:
            raise DoctrineError(
                f"{section.path.name}: a {section.kind} file may not name a tool "
                f"(`{tool}`). Tools are described by their schemas; what "
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


def _first_authority_cycle(outranks: dict[str, tuple[str, ...]]) -> tuple[str, ...] | None:
    """The first cycle in the outranking graph, as the path that closes it.

    Depth-first with the standard three colours: a grey node is on the current
    path, so an edge back into it is the cycle, and the slice of the path from
    that node onwards IS the cycle to report. Returning the path rather than a
    bare `True` is the point — "the graph has a cycle" leaves the reader to find
    it by hand across four files, which is the same work the check exists to
    save.
    """
    on_path: list[str] = []
    state: dict[str, str] = {}

    def walk(name: str) -> tuple[str, ...] | None:
        state[name] = "open"
        on_path.append(name)
        for below in outranks.get(name, ()):
            if state.get(below) == "open":
                return (*on_path[on_path.index(below):], below)
            if below not in state:
                found = walk(below)
                if found is not None:
                    return found
        on_path.pop()
        state[name] = "done"
        return None

    for name in outranks:
        if name not in state:
            found = walk(name)
            if found is not None:
                return found
    return None


def check_authority_graph(sections: Sequence[DoctrineSection]) -> None:
    """`authority_above`/`authority_below` name real things, agree, and do not loop.

    design/17 §6 is honest that the ordering BETWEEN authored files is asserted,
    not mechanical — nothing reads the section tag at inference time. What can
    be checked is that the declaration is internally coherent: every name is
    either another section or one of the terminals, nothing outranks itself, if
    A says B is below it then B says A is above it, and **the outranking
    relation is acyclic**. The symmetry half is what stops one side of a
    re-ordering being written and the other forgotten. The acyclicity half is
    what stops the two sides being written to *agree on something impossible*:
    `a` above `b` and `b` above `a` is perfectly symmetric and perfectly
    meaningless — it declares a precedence that no reader, human or model, can
    satisfy. Both a direct 2-cycle and a longer one passed every check here
    until this was added.

    **It is a graph, not a chain, and the name says so.** The function was
    called `check_authority_chain` and the word was wrong in a way that hid a
    real property of the shipped doctrine: `identity` and `voice` declare
    byte-identical authority fields and neither names the other, and so do
    `silence` and `conduct`. They are UNRANKED PEERS. That is not an oversight
    to correct by ranking them — design/17 §7b's own ordering writes the tier as
    `constraint -> identity/voice -> conduct -> situation -> message content ->
    tool output`, with `identity/voice` deliberately one rung, and `kind` — the
    only part of the authority model design/17 §6 says can be mechanised — puts
    `silence` and `conduct` on one rung by construction, both being
    `kind: conduct`. (The intermediate `capability` rung that used to sit
    between `conduct` and `situation` was deleted with DEC-150.) Inventing an
    order between two files that nothing reads at inference time would be
    asserting a fact no gate could check and no measurement supports.

    So what is declared here is a strict partial order over sections (a total
    order over TIERS, with peers inside a tier), and what is enforced is
    exactly the three properties a strict partial order has: irreflexive, with
    the two declarations of each edge agreeing, and acyclic.
    """
    by_name = {s.name: s for s in sections}
    known = set(by_name) | set(AUTHORITY_TERMINALS)
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
                        f"doctrine section nor one of {', '.join(AUTHORITY_TERMINALS)}"
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
    # Both directions feed the edge set, so acyclicity does not depend on the
    # symmetry check above having already passed — a `b` in `a.authority_above`
    # is the same edge as an `a` in `b.authority_below`, and either alone is
    # enough to close a loop. Terminals are sinks and never become nodes.
    outranks: dict[str, tuple[str, ...]] = {}
    for s in sections:
        below = {n for n in s.authority_below if n in by_name}
        below |= {other.name for other in sections if s.name in other.authority_above}
        outranks[s.name] = tuple(sorted(below))
    cycle = _first_authority_cycle(outranks)
    if cycle is not None:
        raise DoctrineError(
            f"{by_name[cycle[0]].path.name}: the authority declaration is cyclic "
            f"({' > '.join(cycle)}). A section cannot outrank something that "
            "outranks it — the order this declares cannot be satisfied, so one of "
            "those edges is written the wrong way round."
        )


class DoctrineStore:
    def __init__(self, dir: Path, *, strict: bool = True, reload: str = "mtime",
                 canary: str = "", display_name: str = "Nora", trigger: str = "",
                 language_hint: str = "", tool_names: Sequence[str] = (),
                 metrics: Metrics) -> None:
        # `tool_names` is what the tool-catalogue gate checks BACKTICKED text
        # against; the structural half of that gate (bare snake_case) runs
        # whether or not it is supplied, so an omitted list narrows the check
        # rather than disabling it. The composition root passes the registry's
        # own names — see `nora.app.composition`.
        self._dir = Path(dir)
        self._strict = strict
        self._canary = canary
        self._display_name = display_name
        self._trigger = trigger
        self._language_hint = language_hint
        self._tool_names = tuple(tool_names)
        self._cache: dict[str, Doctrine] = {}
        self._generation = 0
        # The mtime/last-good machine is `nora.instructions.reload`, shared with
        # the Dreamer's instruction store. The counter name and the log subject
        # are passed in so `nora_doctrine_reload_failures_total` keeps counting
        # exactly what it always counted, and so a Dreamer failure can never be
        # attributed to the doctrine.
        self._watcher: DirectoryWatcher[tuple[DoctrineSection, ...]] = DirectoryWatcher(
            self._dir, scan=self._scan, metrics=metrics, subject="doctrine",
            failure_counter=RELOAD_FAILURES_COUNTER, error=DoctrineError,
            log=log, glob="**/*.md", reload=reload,
        )

    @property
    def reload_failures(self) -> int:
        """How many live edits have been refused since startup, each of which
        left the previous good doctrine in place. Read by `/health`."""
        return self._watcher.failures

    def _scan(self) -> tuple[DoctrineSection, ...]:
        # Recursive: doctrine lives in scope folders (`core/`, `rooms/`,
        # `instruments/`, and `public/` later) under `self._dir`, not flat.
        # `order` is authoritative for assembly (below); this sort is only
        # processing order, so a folder move cannot change what is served.
        files = sorted(self._dir.glob("**/*.md"), key=lambda p: p.name)
        if not files:
            raise DoctrineError(f"no doctrine files (*.md) in {self._dir}")
        sections: list[DoctrineSection] = []
        for path in files:
            try:
                section = parse_section(path)
                if section.kind in DERIVED_KINDS:
                    raise DoctrineError(
                        f"{path.name}: kind {section.kind!r} is DERIVED and has "
                        "no file. Situation is derived from the running turn; "
                        "an authored copy of it is a cache with no invalidation."
                    )
                check_kind_boundaries(section, self._tool_names)
                check_normative_register(section)
                sections.append(section)
            except DoctrineError:
                if self._strict:
                    raise
                log.warning("skipping invalid doctrine file %s", path.name)
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
        check_authority_graph(sections)
        # `order` is authoritative; filenames break ties.
        return tuple(sorted(sections, key=lambda s: (s.order, s.path.name)))

    def _current_sections(self) -> tuple[DoctrineSection, ...]:
        """The freshest good scan, dropping the assembled cache when it moves.

        Invalidation rides `generation` rather than a callback: the sections
        changed iff a scan SUCCEEDED, and a failed reload deliberately keeps the
        previous good sections, so it must not clear anything.

        **One behaviour the extraction deliberately drops.** The old failure
        path re-seeded the assembled cache from a parallel `_last_good` dict.
        With more than one audience in play that put back an assembly built from
        sections TWO reloads ago — `get("tag")` after a good reload refreshed
        only `"tag"`, so a later failure restored a stale `"heartbeat"` — which
        is the opposite of what "keep the last good doctrine" means. Nothing
        pinned it, and the sections themselves have always been last-good, so
        the cache is now simply left alone: a miss reassembles from the sections
        actually in force.
        """
        sections = self._watcher.current()
        if self._watcher.generation != self._generation:
            self._generation = self._watcher.generation
            self._cache.clear()
        return sections

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
        sections = self._current_sections()
        cached = self._cache.get(applies_to)
        if cached is not None:
            return cached
        selected = tuple(
            s for s in sections
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
        return doctrine
