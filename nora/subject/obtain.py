"""`obtain(spec)` — the only way to get Nora outside production.

One function. No optional collaborators, deliberately: a builder with many
parameters defaulting to `None` lets each caller forget a different thing, and
an eval then measures an agent missing `<standing>`/`<notes>`/`<facts>` with
nothing to notice (`tests/architecture/test_assembler_parity.py`).

`SubjectSpec` therefore names only what is NOT Nora. Adding a field to it is a
decision that something has stopped being part of her, and it should be argued
rather than added because a caller needed it.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import UnionType
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel, TypeAdapter

from nora.app.composition import Dependencies, build_dependencies, wall_clock
from nora.config import Config
from nora.config import load as _load_config
from nora.policy import Conversation, Policy

from .circle import CircleMemory, CircleOverlay
from .environment import IsolatedServices

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: The room a subject uses when its caller names none.
DEFAULT_ROOM = ("signal", "g1")


#: The committed template. ONE place knows its name, the same rule
#: `tests/builders.copy_example_config` holds for the test suite (R-B).
#: NORA AS THE DEPLOYMENT RUNS HER, not as a fresh install would.
#:
#: Not `config.example.toml`, whose JOB is to be a safe template for a new
#: deployment, so it ships with almost everything off: a different
#: `llm.model`, `llm.reasoning_effort`, and `relevance_gate`, `participation`,
#: `standing`, `culture`, `notes`, `dreamer`, `search`, `online`, `weather`,
#: `route` and `preview` all DISABLED. A case run against it would measure a
#: different model, at a different reasoning effort, with most of Nora switched
#: off.
#:
#: The rule "nothing reaches the model that the deployment would not send"
#: constrains the PROMPT; this constrains the READER.
_TEMPLATE = _REPO_ROOT / "config.evaluation.toml"

#: One person, one room, `default = "deny"`. A caller that does not care about
#: identity gets something VALID and CLOSED rather than an accident — the same
#: fail-closed default the real `policy.toml` opens with.
_DEFAULT_IDENTITY = (
    '[persons.miles_novak]\nname = "Miles"\n'
    '[[persons.miles_novak.signal]]\nphone = "+852111"\n'
)
#: What docker calls the machine a container runs on, and what the host
#: itself calls that machine.
_DOCKER_HOST = "host.docker.internal"
_LOOPBACK = "127.0.0.1"


def _resolves(host: str) -> bool:
    import socket

    try:
        socket.gethostbyname(host)
    except OSError:
        return False
    return True


def _from_host(url: str) -> str:
    """`url` with `host.docker.internal`, a name only a container can resolve,
    swapped for loopback; anything else untouched.

    The suite on the host reaches the services a container reaches through
    that name on loopback, where they publish; without this the host would
    try to resolve a name it cannot, and every model call would be unreachable.
    """
    return url.replace(f"//{_DOCKER_HOST}:", f"//{_LOOPBACK}:").replace(
        f"//{_DOCKER_HOST}/", f"//{_LOOPBACK}/"
    )


def resolve_credentials(path: Path) -> dict[str, str]:
    """Resolve credentials for a measurement client without composing a subject."""
    from nora.secret import resolve

    return resolve(path)


def model_endpoint(path: Path | Config, purpose: str, model: str = "") -> tuple[str, str, str]:
    """Resolve a measurement route through the application's configuration,
    reachable from where the measurer runs (the subject's own rewrite)."""
    cfg = path if isinstance(path, Config) else _load_config(path, require_secrets=False)
    url, key, name = cfg.endpoint(purpose, model)
    if (f"//{_DOCKER_HOST}:" in url or f"//{_DOCKER_HOST}/" in url) and not _resolves(_DOCKER_HOST):
        url = _from_host(url)
    return url, key, name


class _Unresolved:
    """A config path the schema cannot give a concrete default for."""

    def __repr__(self) -> str:
        return "UNRESOLVED"


#: `config_default`'s answer for a path that is not a concrete schema leaf —
#: a required field, a runtime-keyed container, a typo. `None` is a real
#: nullable default; `UNRESOLVED` means there is no default to reconstruct.
UNRESOLVED = _Unresolved()


def config_default(key: str) -> object:
    """What `nora/config.py` gives a dotted key nobody states.

    Lives here rather than in `evals/` because the layered contract forbids
    `evals/` importing `nora.config` (`tests/test_boundaries.py`), and this is
    already the eval-facing seam. It answers one question about the CONFIG
    SCHEMA, which is Nora's own, not about evaluation.

    `UNRESOLVED` means UNRESOLVABLE — a required field with no default, or a
    nested container like `dreamer.budgets.<name>` that is keyed at runtime.
    `None` is a real nullable default. Callers must treat `UNRESOLVED` as
    "cannot check", never as "agrees" — and never as the value `None`.
    """
    from pydantic_core import PydanticUndefined

    from nora.config import Config

    model: Any = Config
    parts = key.split(".")
    for index, part in enumerate(parts):
        field = getattr(model, "model_fields", {}).get(part)
        if field is None:
            return UNRESOLVED
        if index == len(parts) - 1:
            default = field.default
            if default is PydanticUndefined:
                default = (field.default_factory()
                           if field.default_factory is not None
                           else PydanticUndefined)
            return (UNRESOLVED if default is PydanticUndefined
                    or hasattr(default, "model_fields") else default)
        annotation = field.annotation
        origin = get_origin(annotation)
        if origin in (Union, UnionType):
            annotation = next(arg for arg in get_args(annotation)
                              if arg is not type(None))
        if not hasattr(annotation, "model_fields"):
            return UNRESOLVED
        model = annotation
    return UNRESOLVED


def config_member_of_runtime_map(key: str) -> bool:
    """Whether a dotted key names a member of a `dict[...]`-typed field.

    `dreamer.budgets.culture.turns` is keyed at runtime: production holds no
    `culture` member it never stated, so omitting the example's member IS
    the parity — unlike a required leaf, whose omission cannot load.
    """
    from nora.config import Config

    model: Any = Config
    parts = key.split(".")
    for index, part in enumerate(parts):
        field = getattr(model, "model_fields", {}).get(part)
        if field is None:
            return False  # an unknown path, not a runtime-keyed member
        if index == len(parts) - 1:
            return False
        annotation = field.annotation
        origin = get_origin(annotation)
        if origin in (Union, UnionType):
            annotation = next(arg for arg in get_args(annotation)
                              if arg is not type(None))
            origin = get_origin(annotation)
        if origin is dict:
            return True
        if not hasattr(annotation, "model_fields"):
            return False
        model = annotation
    return False


def default_policy(room: tuple[str, str], *, unit: str = "nora") -> str:
    """One allowed room, `default = "deny"`, built FROM the room.

    Generated rather than a constant because the room is a parameter, and a
    policy naming a different room than the subject writes into is the
    quietest failure in this layer: ingest refuses admission, no
    turn runs, nothing raises, and every tag case scores a `no_reply` PASS with
    the report reading OK.

    `unit` is the room's social unit (DEC-105). Every unit-keyed read (notes:
    `nora.app.composition._note_unit_of`, the `<notes>` prompt block) resolves
    through the POLICY's `unit`, and so does every unit-keyed WRITE this
    package makes: `obtain()` resolves the room's entry and carries
    `Subject.unit`, so a seed and a turn key under the same value by
    construction rather than by two files agreeing.

    Carries a `label` (the room id, which is what this function is handed and
    all it has) because `load_policy` refuses an allowed room with none as a
    STARTUP error — the same refusal a deployment's own
    `policy.toml` is held to, so a subject that skipped it here would compose
    against a policy shape production could never actually run.
    """
    platform, conversation_id = room
    return (
        'default = "deny"\n'
        f'[[conversations]]\nplatform = "{platform}"\n'
        f'id = "{conversation_id}"\nlabel = "{conversation_id}"\n'
        f'persons = "*"\nunit = "{unit}"\n'
    )


@dataclass(frozen=True, slots=True)
class SubjectSpec:
    """What is NOT Nora, and therefore what a caller states.

    Every field here is a seam — a place the world reaches her. Nothing else is
    settable, because
    everything else IS her.
    """

    #: Where her store is. A caller supplies a database it owns; the graph reads
    #: and writes it exactly as production does.
    database_url: str

    #: A directory the composition may write into — media custody, anything
    #: file-backed. Never a shared location.
    workdir: Path

    #: WHICH CONFIGURATION she composes FROM — the recorded `config.toml` in
    #: force at the case's instant; `None` is the committed template. Same
    #: copy-into-workdir path, same credential custody as the template's.
    config: Path | None = None

    #: Overrides onto the committed template, as dotted paths. This is the seam
    #: for "which doctrine", "which model", "how many samples" — configuration,
    #: which is what a deployment differs from another deployment by.
    #:
    #: NOT a seam for collaborators. `{"llm.model": "nora"}` is legitimate;
    #: there is no way to express `{"media_join": None}` and that is the point.
    overrides: dict[str, Any] | None = None

    #: WHO she knows, as `identity.toml` content. A seam because a case saying
    #: who is in the room is stating INPUT, in the same sense as what somebody
    #: typed — and because `nora.config` resolves `identity_file` relative to the
    #: config file, so every caller would otherwise write this into its own
    #: workdir by hand. Seven assemblies is what "every caller does it by hand"
    #: produces.
    identity: str = _DEFAULT_IDENTITY

    #: WHICH ROOMS she is in, as `policy.toml` content — the DEPLOYMENT'S OWN
    #: file, verbatim, for an eval case (`evals.backbone._drive`); every
    #: entry's `description`, `persons`, `relevance_gate`, `home_room`,
    #: `voice` and `tools` is load-bearing and reaches the model or the
    #: gate. Empty means `default_policy(room)`, so a test that does not
    #: care gets a valid, closed policy naming the room it is actually using.
    #:
    #: A policy that does NOT name `room` is REFUSED at `build_config`, because
    #: it is the quietest failure available: ingest refuses admission, no turn
    #: runs, nothing raises, and every tag case scores a `no_reply` PASS while
    #: the report reads OK.
    policy: str = ""

    #: WHICH ROOM she is in — the REAL room's own `(platform, conversation_id)`
    #: when the case reconstructs one. Cases are isolated by DATABASE, not by
    #: room: each composes against a sandbox database of its own
    #: (`nora.subject.sandbox.sandbox_database`), so the real id is usable and
    #: the deployment's own `policy.toml` names it verbatim.
    room: tuple[str, str] = DEFAULT_ROOM

    #: WHAT TIME IT IS. `None` means the wall clock, which is production.
    #:
    #: A seam because for some of her the answer IS the question: the proactive
    #: adaptor decides whether a note is due, whether a position has gone stale,
    #: whether now is the moment. A case that cannot say when "now" is cannot
    #: ask that adaptor anything — it can only watch it answer about today.
    #:
    #: FROZEN, not offset: the same instant every call, so a turn and the window
    #: it reads cannot drift apart mid-case. That is the property drivers
    #: lose by computing two clock reads a few lines apart, and it is
    #: cheaper to hold here than to remember at each call site.
    now: datetime | None = None

    #: THE CONTROLLED STATE OF THE WORLD AROUND HER — which external boundary
    #: is absent, degraded or held: a subagent job still running or delivered,
    #: the reaction endpoint down, the GIF provider unconfigured, the memory
    #: index partial or out. A seam for `now`'s reason: WHAT THE WORLD IS
    #: DOING is not part of her, and a case that cannot state it can only
    #: watch her on a world that happens to be healthy. `None` is the
    #: unmarked world — every boundary exactly as composed.
    circle: CircleOverlay | None = None

    #: WHERE THE CONTROLLED `sendReaction` ENDPOINT IS. `circle.reaction_wire`
    #: binds a draw-owned RPC address per case; `obtain` points the composed
    #: adapter's `SignalReactor` at it, so the REAL reactor meets the
    #: controlled outage rather than a stub of itself. Empty means no
    #: overlay endpoint — composition is untouched.
    signal_rpc_url: str = ""

    environment: IsolatedServices | None = None


@dataclass(frozen=True, slots=True)
class Subject:
    """Nora, composed, plus the config she was composed from.

    `deps` is exactly what `nora/__main__.py` runs on — same type, same builder,
    same fields — and
    `tests/integration/test_the_subject_is_the_deployed_graph.py` fails when
    `__main__` and the subject diverge. An entry point is an attribute of it (`deps.dispatcher`,
    `deps.engagement`, `deps.participation_trigger`, `deps.dreamer_runtime`),
    so the entry point is a selection on a case rather than a branch in a
    runner.
    """

    deps: Dependencies
    cfg: Config

    #: The room this subject reads and writes. Carried so an entry point does
    #: not have to be told twice.
    room: tuple[str, str]

    #: The database this graph was composed against. Carried because
    #: `Config` does not hold it — production's seam for the store is the
    #: ENVIRONMENT (`cfg.store.database_url_env` names the variable), so the
    #: URL is nowhere on `cfg` to read back.
    database_url: str

    #: The social unit `policy.toml` assigns `room` (DEC-105), and the key the
    #: room's culture chain hangs off (`primary_id`, DEC-181). RESOLVED from
    #: the policy entry the room composes with, never stated by a caller:
    #: both are what production reads for this room, and `GLOSSARY.md` says
    #: neither is a function of `(platform, conversation_id)`. Carried here
    #: because `know()` writes notes and culture under them and would
    #: otherwise re-derive both from a second reading of the same file.
    unit: str
    culture_key: str

    #: The persons `policy.toml` LISTS for `room` (`Conversation.persons`;
    #: `None` is a `persons = "*"` room, which lists nobody). Read by
    #: `sandbox_from_snapshot` to copy the per-unit account of each (the
    #: `<character>` block renders exactly these), so a snapshot asks the
    #: source for the persons production would render and no others.
    listed_persons: frozenset[str] | None = None
    circle_memory: CircleMemory | None = None
    environment: IsolatedServices | None = None

    async def aclose(self) -> None:
        if self.environment is not None:
            await self.environment.close_subject(self)


def _with(obj: Any, path: str, value: Any) -> Any:
    head, _, rest = path.partition(".")
    if rest:
        value = _with(obj[head] if isinstance(obj, dict) else getattr(obj, head), rest, value)
    if isinstance(value, dict) and not isinstance(obj, dict):
        value = TypeAdapter(type(obj).model_fields[head].annotation).validate_python(value)
    return {**obj, head: value} if isinstance(obj, dict) else obj.model_copy(update={head: value})


#: What EVERY non-production caller needs, applied before the caller's own
#: overrides so a caller can still change any of them.
#:
#: These are here rather than in each caller because a caller reinventing them
#: yields divergent assemblies.
def _baseline(cfg: Config, workdir: Path) -> Config:
    # `store.data_dir` — media custody lands in the caller's directory, never a
    # shared one.
    cfg = _with(cfg, "store.data_dir", workdir)
    # Each composed subject owns its journal lock, just like its database
    # and media. The deployment's computer state cannot be shared by draws.
    cfg = _with(cfg, "computer.state_dir", str(workdir / "state"))
    cfg = _with(cfg, "image_gen.state_dir", str(workdir / "state"))
    # `doctrine.dir` and `dreamer.dir` resolve RELATIVE TO THE CONFIG FILE, and
    # the copy lives in the caller's workdir, which has no such subdirectories.
    # The Dreamer's corpus is validated at startup — deliberately, so a
    # malformed objective is not a silent Dreamer three hours later — so an
    # unpointed `[dreamer]` fails composition rather than the thing under test.
    cfg = _with(cfg, "doctrine.dir", _REPO_ROOT / "nora" / "doctrine")
    cfg = _with(cfg, "dreamer.dir", _REPO_ROOT / "nora" / "dreamer")
    return cfg


def build_config(spec: SubjectSpec) -> Config:
    """The template, copied into `spec.workdir`, baselined, then overridden.

    Copied rather than read in place because `nora.config` resolves
    `identity_file`, `policy.toml`, `doctrine.dir` and `dreamer.dir` relative to
    the config file's own directory — so the copy's location decides where those
    are looked for, which is what lets a caller own them without touching the
    repo's.
    """
    spec.workdir.mkdir(parents=True, exist_ok=True)
    # Written before the config loads: `nora.config` resolves both relative to
    # the config file's own directory, and `build_dependencies` reads persons at
    # startup, so an absent `identity.toml` is a `StartupError` rather than an
    # empty roster.
    (spec.workdir / "identity.toml").write_text(spec.identity, encoding="utf-8")
    # `cfg` BEFORE the policy, so the policy is checked against what
    # `know()` will actually write notes under, not a value re-derived from
    # `spec.overrides` by a second reading of the same dict. `nora.config.load()`
    # does not read `policy.toml` to load `config.toml`, so this ordering costs
    # nothing. Re-deriving `stated_unit` from `spec.overrides` with its own
    # hardcoded `"nora"` fallback would pass whenever three literals in three
    # files happened to coincide — convention, not construction.
    cfg_path = spec.workdir / "config.toml"
    shutil.copy(spec.config or _TEMPLATE, cfg_path)
    loaded = _load_config(cfg_path, require_secrets=False)
    # Credential custody stays with the DEPLOYMENT's `secret.toml`, wherever
    # the declared configuration came from: a recorded `in_force/` directory
    # holds none, and pointing resolution at it would silently lose them.
    loaded._secret_path = _TEMPLATE.parent / "secret.toml"
    cfg = _baseline(loaded, spec.workdir)
    for path, value in (spec.overrides or {}).items():
        cfg = _with(cfg, path, value)
    if spec.overrides:
        # Every section an override touched re-validates through the real
        # schema — field constraints and model_validators run before
        # composition; `model_copy` alone skips all of them.
        for name in {p.split(".", 1)[0] for p in spec.overrides}:
            section_type = Config.model_fields[name].annotation
            if (isinstance(section_type, type)
                    and issubclass(section_type, BaseModel)):
                section_type.model_validate(
                    getattr(cfg, name).model_dump(mode="python"),
                    context={"base": spec.workdir})
    # A case-owned environment binds origins itself without DNS discovery.
    if spec.environment is None and not _resolves(_DOCKER_HOST):
        cfg = _with(cfg, "llm.base_url", _from_host(cfg.llm.base_url))
        # Named model endpoints carry their own URL; a route through one would
        # otherwise still dial the container-only name.
        cfg = cfg.model_copy(update={"providers": {
            name: p.model_copy(update={"base_url": _from_host(p.base_url)})
            for name, p in cfg.providers.items()}})
    policy = spec.policy or default_policy(spec.room)
    (spec.workdir / "policy.toml").write_text(policy, encoding="utf-8")
    return cfg


class RoomNotInPolicyError(ValueError):
    """The subject's room is one the policy does not name. Its own type so a
    caller that knows WHY a room can be unnamed (a pseudonymised eval anchor)
    can say so without matching on this message."""


def _conversation_for(spec: SubjectSpec, policy: Policy) -> Conversation:
    """Resolve the room through the production graph's current policy."""
    convo = policy.conversation(*spec.room)
    if convo is None:
        raise RoomNotInPolicyError(
            f"policy names no conversation for the subject's room "
            f"{spec.room!r}; ingest would refuse every message and nothing "
            "would raise"
        )
    if convo.primary_id != spec.room[1]:
        raise ValueError(
            f"the subject's room {spec.room!r} resolves to primary_id "
            f"{convo.primary_id!r}, not its own conversation_id — the "
            "policy names this room under an entry whose id is some OTHER "
            "conversation's, so culture/notes/the Dreamer's scope would key "
            "under the wrong room's identifiers"
        )
    return convo


async def obtain(spec: SubjectSpec) -> Subject:
    """Compose an isolated subject with scoped credentials and database mapping."""
    environment = spec.environment
    if environment is None or not environment.active:
        raise ValueError("obtain requires active case-owned external services")
    if environment.database_url != spec.database_url:
        raise ValueError("subject database differs from its isolated environment")
    if spec.signal_rpc_url or (spec.circle and any((
            spec.circle.memory, spec.circle.reaction, spec.circle.gif))):
        raise ValueError("scenario responses require case-owned service bindings; "
                         "composition substitutions are not production equivalence")
    cfg = environment.bind_config(build_config(spec))
    if spec.now is None:
        clock: Callable[[], datetime] = wall_clock
    else:
        _at = spec.now

        def clock() -> datetime:
            return _at
    # Semantic time is controlled; operational leases and deadlines advance.
    deps = await build_dependencies(cfg, clock=clock, operational_clock=wall_clock)
    subject = None
    try:
        convo = _conversation_for(spec, deps.config_reload.policy)
        subject = Subject(
            deps=deps, cfg=cfg, database_url=spec.database_url, room=spec.room,
            unit=convo.unit, culture_key=convo.primary_id,
            listed_persons=convo.persons, environment=environment,
        )
        environment.register_subject(subject)
        if deps.computer_lifecycle is not None:
            deps.computer_lifecycle.start(deps.background)
        environment.check()
        return subject
    except BaseException:
        if subject is None:
            from nora.app.lifecycle import close_dependencies

            await close_dependencies(deps)
        else:
            await subject.aclose()
        raise


def validate_subject_overrides(overrides: dict[str, Any],
                               base: Config | None = None,
                               context_dir: Path | None = None,
                               ) -> Config | None:
    """Every override's dotted path must resolve to a real `Config` field
    and its value must satisfy the leaf's REAL constraints — `gt`/`le`
    ride the field's metadata, which `rebuild_annotation()` restores for
    `TypeAdapter` (NaN and -1 fail `gt=0`, a truthy string fails a bool).
    Given `base` (the config the overrides land on) the values are
    applied the same `_with` way composition applies them and every
    touched section re-validates — model_validators like
    `run_timeout <= max_run_timeout` run on the merged values. Without a
    base only the per-path checks run; `build_config` re-validates the
    merged config before composition either way. Credentials stay
    named env references, never raw values."""
    for path, value in overrides.items():
        if isinstance(value, str) and ("_key" in path or "secret" in path
                                       or "token" in path) \
                and not path.endswith("_env"):
            raise ValueError(
                f"--subject-overrides: {path!r} looks like a raw credential; "
                "name the env var instead")
        node: Any = Config
        leaf: Any = None
        for part in path.split("."):
            leaf = (node.model_fields.get(part)
                    if hasattr(node, "model_fields") else None)
            if leaf is None:
                raise ValueError(
                    f"--subject-overrides: {path!r} names no config field")
            node = leaf.annotation
        if isinstance(node, type) and hasattr(node, "model_fields"):
            raise ValueError(
                f"--subject-overrides: {path!r} is a section, not a leaf")
        if node is bool and type(value) is not bool:
            raise ValueError(
                f"--subject-overrides: {path!r} wants a real bool, "
                f"not {type(value).__name__} {value!r}")
        TypeAdapter(leaf.rebuild_annotation()).validate_python(value)
        if base is not None:
            base = _with(base, path, value)
    if base is None:
        return None
    for name in {p.split(".", 1)[0] for p in overrides}:
        section_type = Config.model_fields[name].annotation
        if (isinstance(section_type, type)
                and issubclass(section_type, BaseModel)):
            section_type.model_validate(
                getattr(base, name).model_dump(mode="python"),
                context={"base": context_dir or Path.cwd()})
    return base
