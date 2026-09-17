"""`obtain(spec)` — the only way to get Nora outside production.

One function. No optional collaborators, deliberately: `build_offline_pipeline`
takes 21 parameters of which 13 default to `None`, and that is precisely what let
two callers each forget a different thing — the incident
`tests/architecture/test_assembler_parity.py` records (*"composition.py passed 18
keyword arguments and offline.py passed 7, so every eval measured an agent
missing `<standing>`/`<notes>`/`<facts>` with nothing to notice"*).

`SubjectSpec` therefore names only what is NOT Nora. Adding a field to it is a
decision that something has stopped being part of her, and it should be argued in
`design/evaluation/01-boundaries.md` §2 rather than added because a caller needed
it.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from nora.app.composition import Dependencies, build_dependencies, wall_clock
from nora.config import Config
from nora.config import load as _load_config

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: The room a subject uses when its caller names none.
DEFAULT_ROOM = ("signal", "g1")


def room_for(case_name: str) -> tuple[str, str]:
    """A room of this case's own, derived from its name.

    **Cases are not isolated by the database.** `run_once` composes a fresh
    graph per case but hands every one the SAME `database_url`, so without this
    every case writes into one room and reads the others' rows back:

    - the gate reads `recent(limit=40)` — the NEWEST 40 — so from case ~8 its
      window is mostly other cases' transcripts;
    - the Dreamer reads `since(after_id=0, limit=64)` — the OLDEST 64
      (`nora/infra/sql/audit.py:176`, ascending) — so in a 21-case corpus the
      later cases' rows never reach the adaptor AT ALL, and they are graded
      anyway.

    Measured on three gate cases in one database: the third case's own window
    is one row and the gate was shown five. Found by review, not by the tests —
    `pg_url` is per-TEST and every existing test runs at most three cases, so
    the contamination stays under the 40-row window and is invisible there.

    A room rather than a database because the room is already a parameter on
    every function here, it costs nothing, and it isolates two concurrent runs
    against one database as well — which a truncate would not.
    """
    return ("signal", f"g-{hashlib.sha256(case_name.encode()).hexdigest()[:16]}")


#: The committed template. ONE place knows its name, the same rule
#: `tests/builders.copy_example_config` holds for the test suite (R-B, #113).
_TEMPLATE = _REPO_ROOT / "config.example.toml"

#: A SYNTHETIC account, and deliberately not the real one.
#:
#: Any E.164-shaped string short-circuits the startup resolution; the value is
#: never dialled and never reaches a network. The first version hardcoded Nora's
#: real Signal number — which `scrub/README.md` names as a scrub-map target, and
#: which `Dockerfile`'s `COPY nora/ ./nora/` and `pyproject.toml`'s
#: `include = ["nora*"]` put in the image and the wheel. Found by audit.
_PLACEHOLDER_ACCOUNT = "+85200000000"

#: One person, one room, `default = "deny"`. A caller that does not care about
#: identity gets something VALID and CLOSED rather than an accident — the same
#: fail-closed default the real `policy.toml` opens with.
_DEFAULT_IDENTITY = (
    '[persons.miles]\nname = "Miles"\n'
    '[[persons.miles.signal]]\nphone = "+852111"\n'
)
def default_policy(room: tuple[str, str]) -> str:
    """One allowed room, `default = "deny"`, built FROM the room.

    Generated rather than a constant because the room is now per-case
    (`room_for`), and a policy naming a different room than the subject writes
    into is the quietest failure in this layer: ingest refuses admission, no
    turn runs, nothing raises, and every tag case scores a `no_reply` PASS with
    the report reading OK.
    """
    platform, conversation_id = room
    return (
        'default = "deny"\n'
        f'[[conversations]]\nplatform = "{platform}"\n'
        f'id = "{conversation_id}"\npersons = "*"\n'
    )


@dataclass(frozen=True, slots=True)
class SubjectSpec:
    """What is NOT Nora, and therefore what a caller states.

    Every field here is on `design/evaluation/01-boundaries.md` §2's list of
    seams — the places the world reaches her. Nothing else is settable, because
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

    #: WHICH ROOMS she is in, as `policy.toml` content. Same reasoning. Empty
    #: means `default_policy(room)`, so a caller that does not care gets a
    #: valid, closed policy naming the room it is actually using.
    #:
    #: A policy that does NOT name `room` is REFUSED at `build_config`, because
    #: it is the quietest failure available: ingest refuses admission, no turn
    #: runs, nothing raises, and every tag case scores a `no_reply` PASS while
    #: the report reads OK.
    policy: str = ""

    #: WHICH ROOM she is in. Per-case by default (`room_for`), because cases
    #: share one database and one room means one transcript: the gate would
    #: read the newest 40 rows of EVERYBODY'S cases and the Dreamer the oldest
    #: 64, so late cases' own input would never reach it at all.
    room: tuple[str, str] = DEFAULT_ROOM

    #: WHAT TIME IT IS. `None` means the wall clock, which is production.
    #:
    #: A seam because for some of her the answer IS the question: the proactive
    #: adaptor decides whether a note is due, whether a position has gone stale,
    #: whether now is the moment. A case that cannot say when "now" is cannot
    #: ask that adaptor anything — it can only watch it answer about today.
    #:
    #: FROZEN, not offset: the same instant every call, so a turn and the window
    #: it reads cannot drift apart mid-case. That is the property the old
    #: drivers lost by computing two clock reads a few lines apart (issue #213),
    #: and it is cheaper to hold here than to remember at each call site.
    now: datetime | None = None


@dataclass(frozen=True, slots=True)
class Subject:
    """Nora, composed, plus the config she was composed from.

    `deps` is exactly what `nora/__main__.py` runs on — same type, same builder,
    same 38 fields. An entry point is an attribute of it (`deps.dispatcher`,
    `deps.engagement`, `deps.participation_trigger`, `deps.dreamer_runtime`),
    which is why `design/evaluation/01-boundaries.md` §3 can call the entry point
    a selection on a case rather than a branch in a runner.
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


def _with(obj: Any, path: str, value: Any) -> Any:
    head, _, rest = path.partition(".")
    if rest:
        value = _with(getattr(obj, head), rest, value)
    return obj.model_copy(update={head: value})


#: What EVERY non-production caller needs, applied before the caller's own
#: overrides so a caller can still change any of them.
#:
#: These are here rather than in each caller because a caller reinventing them
#: is exactly how seven different assemblies happened. Each was discovered by
#: running `obtain()` and watching it fail — not by reading the code, which is
#: how this list stayed implicit in `tests/test_composition.py`'s fixture for as
#: long as it did.
def _baseline(cfg: Config, workdir: Path) -> Config:
    # `store.data_dir` — media custody lands in the caller's directory, never a
    # shared one.
    cfg = _with(cfg, "store.data_dir", workdir)
    # `platforms.signal.account` — the template's default is empty, meaning
    # "derive from the daemon", and `build_dependencies` then makes a REAL HTTP
    # call to signal-cli at startup and raises `StartupError` when nothing
    # answers. An override returns immediately with no network. Composing Nora
    # reaches the world unless told otherwise; that was measured here, by this
    # call failing.
    cfg = _with(cfg, "platforms.signal.account", _PLACEHOLDER_ACCOUNT)
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
    policy = spec.policy or default_policy(spec.room)
    if f'id = "{spec.room[1]}"' not in policy:
        raise ValueError(
            f"policy does not name the subject's room {spec.room!r}; ingest "
            "would refuse every message and nothing would raise"
        )
    (spec.workdir / "policy.toml").write_text(policy, encoding="utf-8")
    cfg_path = spec.workdir / "config.toml"
    shutil.copy(_TEMPLATE, cfg_path)
    cfg = _baseline(_load_config(cfg_path), spec.workdir)
    for path, value in (spec.overrides or {}).items():
        cfg = _with(cfg, path, value)
    return cfg


async def obtain(spec: SubjectSpec) -> Subject:
    """Compose Nora. One call, one graph, no collaborators passed.

    Costs ~64ms against a template-cloned database
    (`design/evaluation/03-step0-results.md` §3), which is why this is per-case
    rather than session-scoped: a case can be plain data and nothing has to be
    reset between two of them.

    **The database is handed over through the ENVIRONMENT**, because that is
    production's own seam: `composition.py:1026` reads
    `os.environ[cfg.store.database_url_env]` and there is no parameter to pass
    it by. So `spec.database_url` sets that variable for the duration of the
    call and restores it after.

    **The clock is handed over as a parameter**, because `build_dependencies`
    now takes one (it always had the dependency; it wrote the same literal nine
    times instead of naming it). `spec.now` is frozen rather than offset, so
    every component on the graph agrees about the instant.

    This was found by writing `knowledge.py` against `cfg.store.database_url`
    and discovering no such field exists. Before that, `spec.database_url` was
    declared and never read: a caller passing a URL different from the
    environment's got the environment's, silently, and the integration tests
    passed because the two happened to be equal. A seam that is ignored is
    worse than one that is absent — it reads as a promise.

    The mutation is global for the duration of composition. It is the narrowest
    honest option: the alternative is a new parameter on `build_dependencies`,
    which is production surface changed for a non-production caller, and
    `01-boundaries` §5(2) is exactly the rule against that.
    """
    cfg = build_config(spec)
    clock: Callable[[], datetime] = (
        wall_clock if spec.now is None else (lambda at=spec.now: at)
    )
    var = cfg.store.database_url_env
    before = os.environ.get(var)
    os.environ[var] = spec.database_url
    try:
        deps = await build_dependencies(cfg, clock=clock)
    finally:
        if before is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = before
    return Subject(
        deps=deps, cfg=cfg, database_url=spec.database_url, room=spec.room,
    )
