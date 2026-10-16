"""The Dreamer's instruction store: one doctrine, many objectives, one hash.

The tag path's shape, one layer down. `nora.doctrine.loader` assembles the
sections that apply to a turn, hashes the assembled text once, and every audit
row for that turn carries the digest; here the assembly is the Dreamer's own
doctrine plus the running adaptor's objective, and every row that adaptor writes
carries `instruction_sha256` over the pair.

**The hash covers the pair, not the objective alone.** A hash of the objective
names half the input, so an edit to the shared doctrine, which changes every
adaptor at once, would leave no trace on any row. That is the case the column
exists to catch.

**Two artifact kinds, because they have different lifetimes.** The
doctrine is what is true of every Dreamer run and changes when the runtime's
constraints change; an objective is what one adaptor is for and changes when
that one job is retuned. Folding the doctrine into each objective would copy the
read/act line once per adaptor, a hand-maintained catalogue that goes stale
with nothing to catch it (DEC-29).

**The assembled text names its two halves** (`render_part`): a structural
boundary between doctrine and objective, as Nora's own doctrine has. It is a
rendering, not a gate: nothing here validates either half, and the tags carry
`name` only.

**No gates.** The doctrine's `check_kind_boundaries`,
`check_normative_register` and `check_authority_graph` encode rules about Nora's
own prompt — kinds, RFC 2119 register, an authority ordering — and none of them
means anything for a research objective. `kind` here is descriptive.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from ..domain.prompt import escape_attr
from ..domain.provenance import instruction_sha256
from ..obs.metrics import Metrics
from .frontmatter import InstructionError, ParsedDocument, parse_document
from .reload import DirectoryWatcher, Loaded

log = logging.getLogger("nora.instructions.dreamer")

RELOAD_FAILURES_COUNTER = "nora_dreamer_instruction_reload_failures_total"

DOCTRINE_KIND = "dreamer-doctrine"
OBJECTIVE_KIND = "objective"
PRELUDE_KIND = "dreamer-prelude"
SKILL_KIND = "skill"
KINDS = (DOCTRINE_KIND, OBJECTIVE_KIND, PRELUDE_KIND, SKILL_KIND)

CHANGE_POLICIES = ("operator-only", "agent-may-edit")

# Deliberately NOT the doctrine's `_REQUIRED`. `order` has no meaning where
# there is one doctrine and the objectives are selected by name rather than
# concatenated; `authority_above`/`authority_below` describe a precedence
# between doctrine sections that has no analogue here; and `not_here` is a
# boundary statement the doctrine's gates read. Requiring a field no gate reads
# and no assembly renders is how a frontmatter block turns into ceremony.
REQUIRED = ("name", "kind", "version", "applies_to", "description", "change_policy")

_DOCTRINE_SUBDIR = "doctrine"
_OBJECTIVES_SUBDIR = "objectives"
# Their own directories, not more files in `doctrine/`: a second file in
# `doctrine/` fails startup ("expected exactly one Dreamer doctrine file"), and
# `nora/dreamer` is bind-mounted, so it can reach an older image that scans
# only `doctrine/` and `objectives/`. A new directory is one it never reads.
_PRELUDES_SUBDIR = "preludes"
_SKILLS_SUBDIR = "skills"

# The two tags the assembled system prompt is made of. They are DIFFERENT tags
# rather than one `<section kind="…">` — which is what `nora.doctrine.loader`
# renders — because that corpus is N sections of one document under one root and
# this one is exactly two documents with different authority and different
# lifetimes. A shared root element would say they are parts of one thing, which
# is the reading the seam exists to prevent.
DOCTRINE_TAG = "dreamer_doctrine"
OBJECTIVE_TAG = "dreamer_objective"


def render_part(*, tag: str, name: str, body: str) -> str:
    """Exactly the bytes one half of the assembled prompt contributes.

    `nora.doctrine.loader.render_section`'s shape, and it exists for the same
    reason: ONE function, so `for_objective` cannot build the text one way and
    hash it another.

    **`name` and nothing else, where the doctrine's tag also carries
    `description`.** Nora's doctrine descriptions are authored as prompt text;
    these are repo metadata (`dreamer.md`'s ends with a note about which loader
    gates do not run on the file), and rendering them would put that into the
    system prompt of every Dreamer run.

    `name` is safe to promote because it is the one field already constrained to
    `NAME_RE` — `nora.instructions.frontmatter` keeps it to a charset that
    "needs no escaping anywhere" precisely because it is used as an attribute
    value. It is escaped here regardless, for the reason `nora.domain.prompt`
    gives: escaping costs nothing and skipping it is the habit R-13 forbids.

    **An attribute says what a part IS, never what it contains.** A `tools="…"`
    or an `applies_to="…"` here would be the hand-maintained catalogue
    reinvented in a new location, which is the rule the doctrine loader already
    writes down for its own tag.
    """
    return "\n".join((
        f'<{tag} name="{escape_attr(name)}">',
        body.strip("\n"),
        f"</{tag}>",
    ))


@dataclass(frozen=True)
class InstructionSet:
    """What one adaptor's run is governed by: the assembled text and its identity.

    `text` is what the runtime hands to `system_prompt=`. `sha256` is the value
    stamped into `instruction_sha256` on every row the run writes. `versions` is
    `{"dreamer": 3, "room-language": 1}` — the same shape the tag path already
    logs as `doctrine_versions={…}`, so a trace can be tied to a revision
    without opening the database.

    Both parts ride one object because they must not be able to disagree: a
    caller that read the text here and the digest from somewhere else would
    eventually stamp a row with the hash of instructions it did not send.
    """

    text: str
    sha256: str
    versions: dict[str, int]


@dataclass(frozen=True)
class DreamerSkill:
    """A skill the Cognition session injects after its instruction, per agenda
    kind (`AgendaSkills`).

    `instructions` is exactly the text injected (the body, its leading and
    trailing blank lines dropped) and `sha256` is over exactly that, so the
    value a session row records in `skill_versions` names what the model read;
    the skill's `name` is the key it is recorded under and the heading it is
    injected beneath, so the pair names the whole injected part. The file
    itself is one of the corpus's files, so each load of it is also kept,
    content-addressed, in `revision_blob` (`nora.app.revisions`)."""

    name: str
    instructions: str
    sha256: str
    version: int


@dataclass(frozen=True)
class _Corpus:
    doctrine: ParsedDocument
    objectives: dict[str, ParsedDocument]
    #: Objective name -> the prelude it reads INSTEAD of the shared doctrine.
    preludes: dict[str, ParsedDocument]
    skills: dict[str, ParsedDocument]


class DreamerInstructions:
    """`nora/dreamer/` — `doctrine/` (exactly one file), `objectives/`, and
    `preludes/` and `skills/`, which only the Cognition session reads so far.

    **A prelude replaces the shared doctrine for the objectives it names**
    (`applies_to`, objective names). The shared doctrine tells every Dreamer
    «Nobody is waiting for you, nobody sees this… You look; you do not act»,
    which is true of a judge and false of the Cognition session, whose verbs
    write the group's notebook. Editing the
    shared file would change every adaptor's instruction and digest at once;
    a prelude changes the one adaptor that needs it, and every other adaptor's
    assembled text stays byte-identical
    (`tests/unit/test_dreamer_instruction_goldens.py`). The digest still covers
    the pair, the prelude in the doctrine's place.

    **A skill** is a text the Cognition session injects after its instruction,
    per agenda kind (`DreamerSkill`, `skill`). It lives here, not in
    `nora/skills/`, so it hot-reloads on the bind mount with the rest of the
    Dreamer's text and each load of it is recorded with the corpus
    (`doctrine_revision`, corpus `dreamer`).

    Hot-reloads on mtime through `nora.instructions.reload.DirectoryWatcher`, so
    an operator editing an objective on the bind mount sees it on the next tick
    rather than after a rebuild.

    `strict` follows `DoctrineStore`'s meaning — a malformed file aborts the
    scan, or, lenient, is skipped with a warning. Skipping is not silent
    tolerance: an objective that fails to parse is simply absent, and
    `for_objective` refuses to assemble the doctrine on its own, so the adaptor
    that wanted it fails loudly instead of running against half its
    instructions.
    """

    def __init__(
        self, dir: Path, *, reload: str = "mtime", strict: bool = True,
        metrics: Metrics,
    ) -> None:
        self._dir = Path(dir)
        self._strict = strict
        self._cache: dict[str, InstructionSet] = {}
        self._generation = 0
        self._watcher: DirectoryWatcher[_Corpus] = DirectoryWatcher(
            self._dir, scan=self._scan, metrics=metrics, subject="dreamer_instructions",
            failure_counter=RELOAD_FAILURES_COUNTER, error=InstructionError,
            log=log, glob="*/*.md", reload=reload,
        )

    @property
    def reload_failures(self) -> int:
        return self._watcher.failures

    def loaded(self) -> Loaded:
        """The Dreamer's doctrine and objective files currently served and
        when they were loaded (`nora.app.revisions` records each load)."""
        return self._watcher.loaded()

    def for_objective(self, name: str) -> InstructionSet:
        """The doctrine plus one objective, assembled, hashed and versioned.

        Each half arrives inside its own tag (`render_part`), so the model sees
        where a second document with different authority begins, matching the
        XML-framed INPUT that follows as the first user message
        (`<transcript>`, `<notes>`, `<standing_prior>`, every `ContextBlock`).
        """
        corpus = self._current()
        cached = self._cache.get(name)
        if cached is not None:
            return cached
        objective = self._objective(corpus, name)
        # The objective's own prelude when it has one, else the shared doctrine:
        # the half that says what the runtime is, under the same tag either way.
        doctrine = corpus.preludes.get(name, corpus.doctrine)
        # The joiner is `nora.domain.provenance.instruction_sha256`'s, which is
        # `nora.doctrine.loader._assemble`'s, which is "\n". Assembling the text
        # one way and hashing it another is how a digest stops naming what was
        # sent, so the text below IS the hashed string.
        parts = (
            render_part(tag=DOCTRINE_TAG, name=doctrine.name, body=doctrine.body),
            render_part(
                tag=OBJECTIVE_TAG, name=objective.name, body=objective.body,
            ),
        )
        instructions = InstructionSet(
            text="\n".join(parts),
            sha256=instruction_sha256(*parts),
            versions={
                doctrine.name: _version(doctrine),
                objective.name: _version(objective),
            },
        )
        self._cache[name] = instructions
        return instructions

    def objective_body(self, name: str) -> str:
        """One objective's body, exactly as it is on disk.

        The seam the two observers hold (their `_ObjectiveSource` duck-type): a
        SOURCE rather than a string, because the text hot-reloads and an
        observer must re-read it per tick rather than capture it at
        construction.
        """
        return self._objective(self._current(), name).body

    def _objective(self, corpus: _Corpus, name: str) -> ParsedDocument:
        """One objective by name, or a refusal that names what IS there.

        An unknown name RAISES rather than assembling the doctrine alone. A
        Dreamer run against its doctrine and no objective has no research goal
        at all — it would produce output that looks ordinary and is answering
        nothing, which is worse than a loud startup failure, and it would stamp
        those rows with a digest that names an instruction set no adaptor asked
        for.
        """
        objective = corpus.objectives.get(name)
        if objective is None:
            known = ", ".join(sorted(corpus.objectives)) or "(none)"
            raise InstructionError(
                f"unknown Dreamer objective {name!r} in {self._dir / _OBJECTIVES_SUBDIR} "
                f"— have: {known}"
            )
        return objective

    def objective_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._current().objectives))

    def skill(self, name: str) -> DreamerSkill | None:
        """One skill by name, as the corpus serves it now (hot-reloaded), or
        None when no skill of that name is loaded: `AgendaSkills` turns a None
        into the refusal that names it."""
        doc = self._current().skills.get(name)
        if doc is None:
            return None
        instructions = doc.body.strip("\n")
        return DreamerSkill(
            name=doc.name, instructions=instructions,
            sha256=hashlib.sha256(instructions.encode("utf-8")).hexdigest(),
            version=_version(doc),
        )

    def versions(self) -> dict[str, int]:
        """The doctrine's version and every objective's, as one mapping.

        The corpus-wide sibling of `InstructionSet.versions`, which names only
        the two parts one adaptor assembled. This one is what a Dreamer TICK
        logs, the way a tag start logs
        `doctrine_versions={…}`: a line that ties a trace to a revision without
        anyone opening the database.
        """
        corpus = self._current()
        versions = {corpus.doctrine.name: _version(corpus.doctrine)}
        for name in sorted(corpus.objectives):
            versions[name] = _version(corpus.objectives[name])
        for doc in (*corpus.preludes.values(), *corpus.skills.values()):
            versions[doc.name] = _version(doc)
        return versions

    def _current(self) -> _Corpus:
        corpus = self._watcher.current()
        if self._watcher.generation != self._generation:
            self._generation = self._watcher.generation
            self._cache.clear()
        return corpus

    def _scan(self) -> _Corpus:
        doctrine_files = sorted((self._dir / _DOCTRINE_SUBDIR).glob("*.md"))
        if not doctrine_files:
            raise InstructionError(
                f"no Dreamer doctrine file (*.md) in {self._dir / _DOCTRINE_SUBDIR}"
            )
        if len(doctrine_files) > 1:
            names = ", ".join(p.name for p in doctrine_files)
            # Not a merge, and not "first one wins". There is exactly one
            # Dreamer doctrine because the hash is over doctrine + objective and
            # a second file would make the assembly order — and therefore the
            # digest — depend on a filename nobody chose deliberately.
            raise InstructionError(
                f"{self._dir / _DOCTRINE_SUBDIR}: expected exactly one Dreamer "
                f"doctrine file, found {len(doctrine_files)} ({names})"
            )
        doctrine = self._parse(doctrine_files[0], expected_kind=DOCTRINE_KIND)
        if doctrine is None:                      # lenient mode, broken doctrine
            raise InstructionError(
                f"{doctrine_files[0].name}: the Dreamer doctrine did not parse, and "
                "there is no assembly without it"
            )
        objectives: dict[str, ParsedDocument] = {}
        for path in sorted((self._dir / _OBJECTIVES_SUBDIR).glob("*.md")):
            parsed = self._parse(path, expected_kind=OBJECTIVE_KIND)
            if parsed is None:
                continue
            if parsed.name in objectives:
                raise InstructionError(
                    f"duplicate objective name {parsed.name!r}: "
                    f"{objectives[parsed.name].path.name}, {path.name}"
                )
            objectives[parsed.name] = parsed
        return _Corpus(
            doctrine=doctrine, objectives=objectives,
            preludes=self._preludes(objectives),
            skills=self._named(_SKILLS_SUBDIR, SKILL_KIND),
        )

    def _named(self, subdir: str, kind: str) -> dict[str, ParsedDocument]:
        """Every file of one kind in its own directory, by name; an absent
        directory is none of them."""
        out: dict[str, ParsedDocument] = {}
        for path in sorted((self._dir / subdir).glob("*.md")):
            parsed = self._parse(path, expected_kind=kind)
            if parsed is None:
                continue
            if parsed.name in out:
                raise InstructionError(
                    f"duplicate {kind} name {parsed.name!r}: "
                    f"{out[parsed.name].path.name}, {path.name}"
                )
            out[parsed.name] = parsed
        return out

    def _preludes(
        self, objectives: dict[str, ParsedDocument],
    ) -> dict[str, ParsedDocument]:
        """Objective name -> its prelude, from each prelude's `applies_to`.

        An objective named by two preludes is refused for the reason a second
        doctrine file is: which one is served, and so the digest, would depend
        on a filename. A prelude naming no loaded objective is refused too: a
        typo there would serve the shared doctrine to the objective it meant,
        silently, which is the very text the prelude exists to replace."""
        by_objective: dict[str, ParsedDocument] = {}
        for prelude in self._named(_PRELUDES_SUBDIR, PRELUDE_KIND).values():
            for name in _applies_to(prelude):
                if name not in objectives:
                    # Lenient mode skipped a broken objective: it is absent, and
                    # `for_objective` already fails it loudly by name.
                    if not self._strict:
                        continue
                    raise InstructionError(
                        f"{prelude.path.name}: applies_to names {name!r}, which is not a "
                        f"loaded objective"
                    )
                if name in by_objective:
                    raise InstructionError(
                        f"objective {name!r} has two preludes: "
                        f"{by_objective[name].path.name}, {prelude.path.name}"
                    )
                by_objective[name] = prelude
        return by_objective

    def _parse(self, path: Path, *, expected_kind: str) -> ParsedDocument | None:
        try:
            parsed = parse_document(path, required=REQUIRED, error=InstructionError)
            kind = str(parsed.meta.get("kind"))
            if kind not in KINDS:
                raise InstructionError(
                    f"{path.name}: kind {kind!r} is not one of {', '.join(KINDS)}"
                )
            if kind != expected_kind:
                raise InstructionError(
                    f"{path.name}: kind {kind!r} does not belong in "
                    f"{path.parent.name}/ — that directory holds {expected_kind!r}"
                )
            change_policy = str(parsed.meta.get("change_policy"))
            if change_policy not in CHANGE_POLICIES:
                raise InstructionError(
                    f"{path.name}: change_policy {change_policy!r} is not one of "
                    f"{', '.join(CHANGE_POLICIES)}"
                )
            _version(parsed)                      # int(), or an InstructionError
            if kind == PRELUDE_KIND:
                _applies_to(parsed)               # a list of names, or an InstructionError
        except InstructionError:
            if self._strict:
                raise
            log.warning("skipping invalid Dreamer instruction file %s", path.name)
            return None
        return parsed


def _applies_to(doc: ParsedDocument) -> list[str]:
    """A prelude's `applies_to`: a non-empty list of objective names, and nothing else.

    Anything else (a number, a bool, a bare string, a nested list, a mapping,
    a null) is an `InstructionError`, because the reload watcher tolerates only
    that error: a `TypeError` out of here would escape it and fail every
    `for_objective` until the file was fixed, and at startup would crash-loop
    the process. A list holding a non-string is refused for the same reason,
    and so that an unhashable item never reaches a dict lookup. An EMPTY list is
    refused for `_preludes`'s own reason: a prelude that applies to nothing is
    the same silent fallback to the shared doctrine as a typo in a name."""
    applies = doc.meta.get("applies_to")
    if not applies or not isinstance(applies, list) or not all(
        isinstance(n, str) for n in applies
    ):
        raise InstructionError(
            f"{doc.path.name}: applies_to must be a non-empty list of objective names, "
            f"got {applies!r}"
        )
    return list(applies)


def _version(doc: ParsedDocument) -> int:
    try:
        return int(doc.meta["version"])
    except (KeyError, TypeError, ValueError) as exc:
        raise InstructionError(
            f"{doc.path.name}: version must be an integer"
        ) from exc
