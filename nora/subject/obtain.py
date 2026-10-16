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

import contextlib
import dataclasses
import os
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import tomlkit

from nora.app.composition import Dependencies, build_dependencies, wall_clock
from nora.app.memory_routing import build_workspace_router
from nora.config import Config
from nora.config import load as _load_config
from nora.persons import load_persons
from nora.policy import Conversation, load_policy

from .memory_workspace import (
    NotADrawWorkspaceError,
    refuse_unless_draw_workspace,
    shared_eval_workspace,
)
from .recording_subagent import recording_subagent_worker
from .sandbox_signal import landed_signal_sends

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

#: `miles_novak`, matching the old driver's `_EVAL_PERSON_ID`, and it matters.
#:
#: A conclusion is keyed by the Honcho PEER id, which IS the person id, and the
#: two participation corpora spell `miles_novak`. Keyed under any other id the
#: conclusion is written and cannot be read — `representation` looks up
#: `target or peer_id` and an unknown peer answers reachable-and-knowing-
#: nothing — so the `-known` half of a twin pair would come back identical to
#: its `-unknown` half and the pair would report that the knowledge cut works
#: having cut nothing.
#:
#: A SYNTHETIC account, and deliberately not the real one.
#:
#: Any E.164-shaped string short-circuits the startup resolution; the value is
#: never dialled and never reaches a network. It is not Nora's real Signal
#: number, which `scrub/README.md` names as a scrub-map target and which
#: `Dockerfile`'s `COPY nora/ ./nora/` and `pyproject.toml`'s
#: `include = ["nora*"]` would put in the image and the wheel.
_PLACEHOLDER_ACCOUNT = "+85200000000"
#: An env var this process owns, so an enabled Discord adapter authenticates
#: against nothing instead of the deployment's real bot. The value is the
#: variable's NAME, not a credential — hence the S105 suppression.
_PLACEHOLDER_TOKEN_ENV = "NORA_EVAL_SUBJECT_DISCORD_TOKEN"  # noqa: S105

#: Credentials an ENABLED feature demands before composition will start, or
#: whose absence downgrades a tool the deployment's Nora holds. Filled with
#: `setdefault`, so the deploy host's real values always win.
_CREDENTIALS_ENABLED_FEATURES_DEMAND = (
    "GOOGLE_MAPS_API_KEY",      # [route]
    "OMNIROUTE_API_KEY",        # the model key Nora herself runs on
)

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
#: The name nora's container reaches Honcho by on the private `honcho_memory`
#: network (DEC-259). It exists only inside that network; the host
#: reaches the same service on loopback, where Honcho still publishes.
_HONCHO_ALIAS = "honcho"


def _resolves(host: str) -> bool:
    import socket

    try:
        socket.gethostbyname(host)
    except OSError:
        return False
    return True


def _from_host(url: str) -> str:
    """`url` with a name only a container can resolve (`host.docker.internal`,
    or `honcho` on the private Honcho network) swapped for loopback; anything
    else untouched.

    `honcho` is here because nora reaches Honcho over a private network of its
    own (DEC-259). Honcho also publishes on loopback, so the suite on the host
    reaches the same service there; without this the host would try to resolve
    a name it cannot, and every case would read as "memory unreachable".
    """
    for name in (_DOCKER_HOST, _HONCHO_ALIAS):
        url = url.replace(f"//{name}:", f"//{_LOOPBACK}:").replace(
            f"//{name}/", f"//{_LOOPBACK}/"
        )
    return url


def config_default(key: str) -> object:
    """What `nora/config.py` gives a dotted key nobody states.

    Lives here rather than in `evals/` because the layered contract forbids
    `evals/` importing `nora.config` (`tests/test_boundaries.py`), and this is
    already the eval-facing seam. It answers one question about the CONFIG
    SCHEMA, which is Nora's own, not about evaluation.

    `None` means UNRESOLVABLE — a required field with no default, or a nested
    container like `dreamer.budgets.<name>` that is keyed at runtime. Callers
    must treat that as "cannot check", never as "agrees".
    """
    from pydantic_core import PydanticUndefined

    from nora.config import Config

    model: Any = Config
    parts = key.split(".")
    for index, part in enumerate(parts):
        field = getattr(model, "model_fields", {}).get(part)
        if field is None:
            return None
        if index == len(parts) - 1:
            if field.default is PydanticUndefined:
                return None
            return None if hasattr(field.default, "model_fields") else field.default
        model = field.annotation
        if not hasattr(model, "model_fields"):
            return None
    return None


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
    #:
    #: NOT `cfg.memory.workspace_id`. That is Honcho's workspace, one per
    #: deployment (`nora`), while a unit is per room (`friends`, `ridgeline`);
    #: the two are distinct.
    unit: str
    culture_key: str

    #: The persons `policy.toml` LISTS for `room` (`Conversation.persons`;
    #: `None` is a `persons = "*"` room, which lists nobody). Read by
    #: `sandbox_from_snapshot` to copy the per-unit account of each (the
    #: `<character>` block renders exactly these), so a snapshot asks the
    #: source for the persons production would render and no others.
    listed_persons: frozenset[str] | None = None


def _with(obj: Any, path: str, value: Any) -> Any:
    head, _, rest = path.partition(".")
    if rest:
        value = _with(getattr(obj, head), rest, value)
    return obj.model_copy(update={head: value})


#: What EVERY non-production caller needs, applied before the caller's own
#: overrides so a caller can still change any of them.
#:
#: These are here rather than in each caller because a caller reinventing them
#: yields divergent assemblies.
def _baseline(cfg: Config, workdir: Path) -> Config:
    # `store.data_dir` — media custody lands in the caller's directory, never a
    # shared one.
    cfg = _with(cfg, "store.data_dir", workdir)
    # `platforms.signal.account` — the template's default is empty, meaning
    # "derive from the daemon", and `build_dependencies` then makes a REAL HTTP
    # call to signal-cli at startup and raises `StartupError` when nothing
    # answers. An override returns immediately with no network. Composing Nora
    # reaches the world unless told otherwise. It does not cover a
    # send: `landed_signal_sends` below does.
    cfg = _with(cfg, "platforms.signal.account", _PLACEHOLDER_ACCOUNT)
    # `platforms.discord.token_env` — the SAME neutralisation, for the reason
    # above. The adapters stay ENABLED as the deployment has them, because the
    # registry's `self_aliases()` feeds `self_ids` and `self_ids` is what
    # `MentionPolicy`/`ReplyToNoraPolicy` match on — with the deployment's text
    # trigger off, mention-and-reply IS the engagement path, so a disabled
    # adapter would delete it. What must not reach the world is the CREDENTIAL,
    # and Discord's lives in an env var rather than the config
    # (`nora/platform/registry.py` refuses to start without it), so the
    # variable is pointed at one this process owns.
    os.environ.setdefault(_PLACEHOLDER_TOKEN_ENV, "eval-subject-not-a-real-token")
    cfg = _with(cfg, "platforms.discord.token_env", _PLACEHOLDER_TOKEN_ENV)
    # Every credential an ENABLED feature demands at startup.
    #
    # `setdefault`, which is the whole point: run this ON the deploy host with
    # its environment and the REAL keys win, so the tools are the tools
    # production has. Run it anywhere else and composition still succeeds, with
    # those tools degrading to unavailable — which `composition.py` already
    # warns about per feature rather than failing.
    #
    # THAT DEGRADATION IS A DIVERGENCE, and the honest place to say so is here:
    # production's Nora holds `web_search`, `route`, `online`, and
    # `generate_image`; a subject composed without their keys does not, and a
    # case whose turn would have reached one measures a Nora missing it. Full
    # fidelity means running where the deployment runs.
    for name in _CREDENTIALS_ENABLED_FEATURES_DEMAND:
        os.environ.setdefault(name, "eval-subject-placeholder")
    # `memory.workspace_id` — Honcho is the ONE store the sandbox database
    # does not isolate: it is an external service keyed by workspace, and the
    # deployment's workspace is where every real peer card lives. An eval
    # turn's ingest FEEDS Honcho (`nora/ingest/stages/memory_feed.py`), so a
    # subject composed with the deployment's workspace writes eval speech
    # into production's peer representations.
    #
    # The fixed `-eval` sibling is the DEFAULT only. An eval draw overrides it
    # with a workspace of its own (`memory.workspace_id`, from
    # `memory_workspace.draw_workspace`), because the shared sibling made
    # draws correlated: the tag path read earlier draws' conclusions back.
    # Configuration, not a new seam: which workspace is a value
    # one deployment differs from another by, which is what `overrides` is
    # for, and `build_config` refuses any value outside the eval namespace.
    cfg = _with(cfg, "memory.workspace_id", shared_eval_workspace(cfg.memory.workspace_id))
    # THE HOST, FROM THE HOST. The deployment's config reaches OmniRoute at
    # `host.docker.internal`, the name docker gives a container for the machine
    # it runs on, and Honcho at `honcho`, a name on a private docker network
    # (DEC-259). The suite runs on that machine, outside a
    # container, where the name does not resolve at all — so the model call
    # would fail before the turn and read as "the model was unreachable" for
    # every case. The same services, reached from the host, are on loopback;
    # rewriting the HOSTNAME alone is the same endpoint at the address the
    # caller can use, the shape `SKIP_PREFIX` already gives every other
    # endpoint. Only when the name does not resolve here: inside a container
    # it does, and nothing is touched.
    if not _resolves(_DOCKER_HOST):
        cfg = _with(cfg, "llm.base_url", _from_host(cfg.llm.base_url))
        cfg = _with(cfg, "memory.base_url", _from_host(cfg.memory.base_url))
    # `doctrine.dir` and `dreamer.dir` resolve RELATIVE TO THE CONFIG FILE, and
    # the copy lives in the caller's workdir, which has no such subdirectories.
    # The Dreamer's corpus is validated at startup — deliberately, so a
    # malformed objective is not a silent Dreamer three hours later — so an
    # unpointed `[dreamer]` fails composition rather than the thing under test.
    cfg = _with(cfg, "doctrine.dir", _REPO_ROOT / "nora" / "doctrine")
    cfg = _with(cfg, "dreamer.dir", _REPO_ROOT / "nora" / "dreamer")
    return cfg


def _refuse_a_workspace_outside_the_eval_namespace(workspace: str, deployment: str) -> None:
    """A subject's Honcho workspace is the shared `<deployment>-eval` sibling
    or a draw's `<deployment>-eval-...`, and nothing else.

    Held on the COMPOSED config, after every override, because an eval draw
    names its workspace through `spec.overrides`, and an override of `nora`
    would feed eval speech into production's peer cards with nothing raising.
    """
    if workspace == shared_eval_workspace(deployment):
        return
    try:
        refuse_unless_draw_workspace(workspace, deployment)
    except NotADrawWorkspaceError as exc:
        raise ValueError(
            f"a subject's Honcho workspace must be {shared_eval_workspace(deployment)!r} "
            f"or a draw's ({exc})"
        ) from exc


def memory_endpoint() -> tuple[str, str]:
    """`(base_url, deployment_workspace)` for Honcho, as a subject reaches it.

    Read from the same template `build_config` copies, with the same
    host-from-the-host rewrite `_baseline` applies, so the workspace a draw
    creates is on the server its subject will talk to. The second value is
    the DEPLOYMENT's workspace, before `_baseline` suffixes it: the prefix
    every draw workspace is derived from and guarded by.
    """
    cfg = _load_config(_TEMPLATE, require_secrets=False)
    base_url = cfg.memory.base_url
    if not _resolves(_DOCKER_HOST):
        base_url = _from_host(base_url)
    return base_url, cfg.memory.workspace_id


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
    shutil.copy(_TEMPLATE, cfg_path)
    loaded = _load_config(cfg_path)
    deployment = loaded.memory.workspace_id
    cfg = _baseline(loaded, spec.workdir)
    for path, value in (spec.overrides or {}).items():
        cfg = _with(cfg, path, value)
    _refuse_a_workspace_outside_the_eval_namespace(cfg.memory.workspace_id, deployment)
    policy = spec.policy or default_policy(spec.room)
    (spec.workdir / "policy.toml").write_text(policy, encoding="utf-8")
    _pin_memory_to_the_eval_workspace(spec.workdir, cfg.memory.workspace_id, deployment)
    _conversation_for(spec)
    return cfg


def _pin_memory_to_the_eval_workspace(workdir: Path, workspace: str, deployment: str) -> None:
    """Every Honcho workspace this subject can route to is `workspace` — the
    eval namespace's — and that is measured, not assumed (DEC-214).

    Rewriting `memory.workspace_id` alone is not the guarantee: a policy with
    `[units]`
    routes each unit to the Honcho workspace its Nora workspace is BOUND to
    in `identity.toml`, and the eval suite composes against the deployment's
    own policy and identity by default — whose bindings name production's
    live workspaces. Composed as written, a subject would feed eval speech
    into `nora` itself with nothing raising.

    So the copies in `workdir` are rewritten to ONE workspace. A policy that
    declares `[units]` keeps it — the subject stays strict and scoped, the
    way production reads — but every unit is put in one workspace, `eval`,
    and `identity.toml`'s `[workspaces]` becomes that one binding, to
    `memory.workspace_id` (already the eval namespace's). A policy without
    `[units]` loses any `[workspaces]` table and stays implicit. Every room,
    unit and scope survives; the divergence from production is that two Nora
    workspaces' people share this ONE throwaway workspace's peers. Then the
    router production itself builds from these files is asked where it can
    reach, and anything outside the eval namespace refuses the subject. That
    check is the guarantee; the rewrite is only what makes it pass.
    """
    policy_path, identity_path = workdir / "policy.toml", workdir / "identity.toml"
    policy_doc = tomlkit.parse(policy_path.read_text(encoding="utf-8"))
    identity_doc = tomlkit.parse(identity_path.read_text(encoding="utf-8"))
    strict = "units" in policy_doc
    if strict:
        for unit in policy_doc["units"].values():  # type: ignore[union-attr]
            unit["workspace"] = _EVAL_WORKSPACE
        policy_path.write_text(tomlkit.dumps(policy_doc), encoding="utf-8")
    if "workspaces" in identity_doc:
        del identity_doc["workspaces"]
    if strict:
        binding = tomlkit.table()
        binding.add(_EVAL_WORKSPACE, {"honcho_workspace": workspace})
        identity_doc["workspaces"] = binding
    identity_path.write_text(tomlkit.dumps(identity_doc), encoding="utf-8")
    router = build_workspace_router(
        policy=load_policy(policy_path),
        bindings=load_persons(identity_path).honcho_workspaces,
        default_workspace=workspace,
    )
    for reachable in router.workspaces():
        _refuse_a_workspace_outside_the_eval_namespace(reachable, deployment)


#: The one Nora workspace an eval subject's units are put in.
_EVAL_WORKSPACE = "eval"


class RoomNotInPolicyError(ValueError):
    """The subject's room is one the policy does not name. Its own type so a
    caller that knows WHY a room can be unnamed (a pseudonymised eval anchor)
    can say so without matching on this message."""


def _conversation_for(spec: SubjectSpec) -> Conversation:
    """The policy entry `spec.room` composes with — RESOLVED, not grepped.

    A substring test over the raw TOML text only proves `spec.room[1]`
    appears as SOME conversation's `id` somewhere in the file — a hand-written
    multi-entry policy can satisfy that while the subject's OWN room resolves
    to a different entry, with a different `primary_id` and a different
    `unit` — so a policy whose OTHER entry carries the room's id string would
    compose cleanly and silently key culture/notes/the Dreamer's scope under a
    real room's identifiers instead of the subject's own.

    A policy that does NOT name the room is REFUSED, because it is the
    quietest failure available: ingest refuses admission, no turn runs,
    nothing raises, and every tag case scores a `no_reply` PASS while the
    report reads OK.
    """
    convo = load_policy(spec.workdir / "policy.toml").conversation(*spec.room)
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
    """Compose Nora. One call, one graph, no collaborators passed.

    Cheap against a template-cloned database, which is why this is per-case
    rather than session-scoped: a case can be plain data and nothing has to be
    reset between two of them.

    **The database is handed over through the ENVIRONMENT**, because that is
    production's own seam: `composition.py` reads
    `os.environ[cfg.store.database_url_env]` and there is no parameter to pass
    it by. So `spec.database_url` goes into a variable of this call's own, named
    uniquely, and `cfg.store.database_url_env` is pointed at it — rather than
    borrowing the deployment's variable and putting it back, which cannot be
    made safe under concurrency.

    **The clock is handed over as a parameter** that `build_dependencies`
    takes. `spec.now` is frozen rather than offset, so
    every component on the graph agrees about the instant.

    `spec.database_url` is honoured: it is the URL the graph is composed
    against, not the environment's.

    Nothing of the deployment's is touched, so there is nothing to restore and
    no window in which a concurrent caller can see the wrong value. A new
    parameter on `build_dependencies` would change production surface for a
    non-production caller.
    """
    cfg = build_config(spec)
    clock: Callable[[], datetime] = (
        wall_clock if spec.now is None else (lambda at=spec.now: at)
    )
    # A VARIABLE OF THIS SUBJECT'S OWN, rather than borrowing the deployment's
    # and putting it back.
    #
    # `build_dependencies` reads the URL from `os.environ[store.database_url_env]`
    # — production's seam is the environment and there is no parameter to pass
    # it by. Setting the DEPLOYMENT's variable and restoring it afterwards is
    # safe to READ (nothing awaits between the set and the read) and unsafe to
    # RESTORE: the restore runs after the whole of `build_dependencies`, which
    # awaits extensively, so two interleaved callers leave the process pointing
    # at a database that is gone.
    #
    # A unique name removes the race instead of serialising it: two subjects
    # never touch one variable, nothing has to be put back, and the runner can
    # be parallelised without this becoming a real bug. The name is per-call
    # rather than per-case so two draws of the same case cannot collide either.
    var = f"NORA_SUBJECT_DATABASE_URL_{uuid.uuid4().hex}"
    cfg = _with(cfg, "store.database_url_env", var)
    os.environ[var] = spec.database_url
    try:
        # Under `[subagent] enabled` the worker is a recorder: the tools are
        # present, nothing runs (`recording_subagent`). A Signal send lands
        # instead of bouncing off a daemon that is not there (`sandbox_signal`).
        with (
            recording_subagent_worker() if cfg.subagent.enabled
            else contextlib.nullcontext(),
            landed_signal_sends(),
        ):
            deps = await build_dependencies(cfg, clock=clock)
    finally:
        os.environ.pop(var, None)
    # NO EMBEDDING WORKER, whatever `[memory_index] embed` says. The flag also
    # switches the graph surface's query embedding on (`composition.py`), so
    # it stays as deployed; the worker is the write half, which only
    # `nora/__main__.py`'s drain loop runs, and a subject must never hold one:
    # with the embedder reachable from the host
    # (`sandbox_index.EMBEDDER_FROM_HOST`) it would write vectors into the
    # sandbox from the live embedder and compete with production's backfill
    # for it. A sandbox's vectors are adopted from the source instead
    # (`nora.subject.sandbox_index`).
    deps = dataclasses.replace(deps, memory_embed=None)
    # The variable is GONE, so the config must not go on naming it: anything
    # that re-read `os.environ[cfg.store.database_url_env]` would get a
    # KeyError. `Subject.database_url` is the field to read, and this makes the
    # dead name unreadable rather than merely unused.
    cfg = _with(cfg, "store.database_url_env", "")
    convo = _conversation_for(spec)
    return Subject(
        deps=deps, cfg=cfg, database_url=spec.database_url, room=spec.room,
        unit=convo.unit, culture_key=convo.primary_id,
        listed_persons=convo.persons,
    )
