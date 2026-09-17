"""The Dreamer's instruction store: one doctrine, many objectives, one hash.

The tag path's shape, one layer down. `nora.doctrine.loader` assembles the
sections that apply to a turn, hashes the assembled text once, and every audit
row for that turn carries the digest; here the assembly is the Dreamer's own
doctrine plus the running adaptor's objective, and every row that adaptor writes
carries `instruction_sha256` over the pair.

**Why the hash covers the pair and not the objective alone** (substrate spec
§4.3). `objective_sha256` was considered and rejected: it names half the input,
so an edit to the shared doctrine — which changes the behaviour of every adaptor
at once, and is therefore the largest blast radius in the subsystem — would
leave no trace on any row anywhere. That is the worst case the column exists to
catch, so it is the case the hash has to cover.

**Two artifact kinds, because they have different lifetimes** (spec §2.1). The
doctrine is what is true of every Dreamer run and changes when the runtime's
constraints change; an objective is what one adaptor is for and changes when
that one job is retuned. Folding the doctrine into each objective would copy the
read/act line once per adaptor, which is the hand-maintained-catalogue shape
DEC-29 already showed goes stale with nothing to catch it.

**The assembled text names its two halves** (`render_part`). This is D9's
second bullet — *"give the assembled text a structural boundary between
doctrine and objective, as Nora's own doctrine has"* — the one bullet of that
decision D10 strengthens rather than retires, since a doctrine that has stopped
making claims about the job still has to be legible as a different document
from the one that does. It is a rendering, not a gate: nothing here validates
either half, and the tags carry `name` and `description` only.

**No gates.** The doctrine's `check_kind_boundaries`,
`check_normative_register` and `check_authority_graph` encode design/17's rules
about Nora's own prompt — kinds, RFC 2119 register, an authority ordering — and
none of them means anything for a research objective (spec §5). `kind` here is
descriptive. If Dreamer objectives want gates later, that is its own decision
with its own evidence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from ..domain.prompt import escape_attr
from ..domain.provenance import instruction_sha256
from ..obs.metrics import Metrics
from .frontmatter import InstructionError, ParsedDocument, parse_document
from .reload import DirectoryWatcher

log = logging.getLogger("nora.instructions.dreamer")

RELOAD_FAILURES_COUNTER = "nora_dreamer_instruction_reload_failures_total"

DOCTRINE_KIND = "dreamer-doctrine"
OBJECTIVE_KIND = "objective"
KINDS = (DOCTRINE_KIND, OBJECTIVE_KIND)

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
    hash it another. That is not a hypothetical — #175 was a served
    `<section …>` attribute that no digest named, and the fix there was this
    function's ancestor.

    **`name` and nothing else, where the doctrine's tag also carries
    `description` — and the difference is measured, not stylistic.** Nora's
    doctrine descriptions were authored as prompt text. These were authored as
    repo metadata, and it shows the moment they are rendered: `dreamer.md`'s is
    five hundred characters that end *"NOTE: none of the doctrine loader's gates
    run on this file … (instruction-substrate spec §5)"*. Rendering it would put
    a note about which startup checks the repo does not run into the system
    prompt of every Dreamer run. Every one of these files is
    `change_policy: operator-only`, so promoting the field would also change
    seven documents' audience without their author's say.

    `name` is safe to promote because it is the one field already constrained to
    `NAME_RE` — `nora.instructions.frontmatter` keeps it to a charset that
    "needs no escaping anywhere" precisely because it is used as an attribute
    value. It is escaped here regardless, for the reason `nora.domain.prompt`
    gives: the cost of doing it is nothing and the cost of the habit is R-13.

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
    without opening the database (spec §4.4).

    Both parts ride one object because they must not be able to disagree: a
    caller that read the text here and the digest from somewhere else would
    eventually stamp a row with the hash of instructions it did not send.
    """

    text: str
    sha256: str
    versions: dict[str, int]


@dataclass(frozen=True)
class _Corpus:
    doctrine: ParsedDocument
    objectives: dict[str, ParsedDocument]


class DreamerInstructions:
    """`nora/dreamer/` — `doctrine/` (exactly one file) and `objectives/`.

    Hot-reloads on mtime through `nora.instructions.reload.DirectoryWatcher`, so
    an operator editing an objective on the bind mount sees it on the next tick
    rather than after a rebuild. That matters more here than for the doctrine:
    the Dreamer has the slowest loop in the system, and an objective that steers
    a research loop will want more revisions than a one-shot prompt did (spec
    §4.1).

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

    def for_objective(self, name: str) -> InstructionSet:
        """The doctrine plus one objective, assembled, hashed and versioned.

        Each half arrives inside its own tag (`render_part`). Before this the
        two were joined by a bare newline — no separator, no heading, no marker
        that a second document with different authority had begun — while the
        adaptor's rendered INPUT, which follows as the first user message, has
        been XML-framed the whole time (`<transcript>`, `<notes>`,
        `<standing_prior>`, and every `ContextBlock`). The model was reading a
        structured lower half under an unstructured upper one.
        """
        corpus = self._current()
        cached = self._cache.get(name)
        if cached is not None:
            return cached
        objective = self._objective(corpus, name)
        # The joiner is `nora.domain.provenance.instruction_sha256`'s, which is
        # `nora.doctrine.loader._assemble`'s, which is "\n". Assembling the text
        # one way and hashing it another is how a digest stops naming what was
        # sent, so the text below IS the hashed string.
        parts = (
            render_part(
                tag=DOCTRINE_TAG, name=corpus.doctrine.name,
                body=corpus.doctrine.body,
            ),
            render_part(
                tag=OBJECTIVE_TAG, name=objective.name, body=objective.body,
            ),
        )
        instructions = InstructionSet(
            text="\n".join(parts),
            sha256=instruction_sha256(*parts),
            versions={
                corpus.doctrine.name: _version(corpus.doctrine),
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

    def versions(self) -> dict[str, int]:
        """The doctrine's version and every objective's, as one mapping.

        The corpus-wide sibling of `InstructionSet.versions`, which names only
        the two parts one adaptor assembled. This one is what a Dreamer TICK
        logs (substrate spec §4.4), the way a tag start logs
        `doctrine_versions={…}`: a line that ties a trace to a revision without
        anyone opening the database.
        """
        corpus = self._current()
        versions = {corpus.doctrine.name: _version(corpus.doctrine)}
        for name in sorted(corpus.objectives):
            versions[name] = _version(corpus.objectives[name])
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
        return _Corpus(doctrine=doctrine, objectives=objectives)

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
        except InstructionError:
            if self._strict:
                raise
            log.warning("skipping invalid Dreamer instruction file %s", path.name)
            return None
        return parsed


def _version(doc: ParsedDocument) -> int:
    try:
        return int(doc.meta["version"])
    except (KeyError, TypeError, ValueError) as exc:
        raise InstructionError(
            f"{doc.path.name}: version must be an integer"
        ) from exc
