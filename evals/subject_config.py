"""The config an eval subject composes: the DEPLOYMENT'S Nora, not a fresh install.

`nora/subject/obtain.py` builds the subject from this config rather than
from `config.example.toml`, whose job is to be a safe template for a NEW
deployment — so it ships with almost everything off, and with a different
`llm.model`, `llm.reasoning_effort`, and `relevance_gate`, `participation`,
`standing`, `culture`, `notes`, `dreamer`, `search`, `online`, `weather`,
`route` and `preview` disabled. A case run against the template would measure
a different model, at a different reasoning effort, with most of Nora switched
off.

The rule "nothing reaches the model that the deployment would not send"
constrains the PROMPT; this module constrains the READER.

GENERATED PER RUN, NEVER VENDORED. The output carries the deployment's real
values — the doctrine canary, room ids, internal endpoints — which is exactly
what `.gitignore` keeps `config.toml` untracked for. Committing a pre-generated
copy would re-publish them under a new name. So this module is the tracked
artefact and its OUTPUT is ignored.

`build()` is PURE — text in, text out — so the rules below are testable against
fixtures without reaching the deployment. That is deliberate: a guard that
needed the real config could only skip when it was absent, and a skipping guard
is the vacuous pass this whole layer exists to refuse.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from pathlib import Path

from nora.subject import (
    UNRESOLVED,
    config_default,
    config_member_of_runtime_map,
)

# Measurement sinks are isolated separately from the subject's functional config.
SKIP_PREFIX: tuple[str, ...] = ("obs.", "evals.langfuse.", "store.data_dir")
FORCE_OFF: tuple[str, ...] = (
    "obs.tracing.enabled", "obs.errors.enabled", "evals.langfuse.enabled",
)
FULL_RESTORE_OVERRIDES: dict[str, object] = {}
NO_GEOCODER_ENV = "NORA_EVAL_NO_GEOCODER"
SUBAGENT_PRECONDITIONS: tuple[str, ...] = ()


def forced_off(values: dict[str, object]) -> tuple[str, ...]:
    """`FORCE_OFF`, less what the deployment's sub-agent needs left alone."""
    if values.get("subagent.enabled") is True:
        return tuple(k for k in FORCE_OFF if k not in SUBAGENT_PRECONDITIONS)
    return FORCE_OFF

_KEY = re.compile(r"^(\s*)([A-Za-z_][\w-]*)(\s*=\s*)(.*)$")
_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _flat(table: dict, prefix: str = ""):
    for key, value in table.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            yield from _flat(value, name)
        else:
            yield name, value


def _fmt(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, dict):
        # A table stays a table. Quoting str(value) would emit "{'a': 1}",
        # which parses back as a string and the drift check refuses.
        return "{" + ", ".join(
            f"{key if _BARE_KEY.match(key) else _fmt(key)} = {_fmt(item)}"
            for key, item in value.items()
        ) + "}"
    if isinstance(value, (list, tuple)):
        # An array stays an array. Quoting its repr would turn
        # `sources = ["google_drive"]` into the STRING "['google_drive']", which the
        # drift check refuses.
        return "[" + ", ".join(_fmt(item) for item in value) + "]"
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def adopted(deployment_toml: str) -> dict[str, object]:
    """Which of the deployment's settings the sandbox takes, by dotted key."""
    values = dict(_flat(tomllib.loads(deployment_toml)))
    taken = {
        k: v for k, v in values.items()
        if not any(k.startswith(prefix) for prefix in SKIP_PREFIX)
    }
    # The bridge example differs from the implicit legacy deployment default.
    # Preserve the effective deployment value rather than changing transports.
    mode_default = config_default("platforms.signal.mode")
    if mode_default is not UNRESOLVED and mode_default is not None:
        taken.setdefault("platforms.signal.mode", mode_default)
    taken.update(dict.fromkeys(forced_off(values), False))
    return taken


def _exempt(key: str) -> bool:
    return any(key.startswith(prefix) for prefix in SKIP_PREFIX)


def build(example_toml: str, deployment_toml: str) -> str:
    """`config.example.toml` with the deployment's behaviour values over it.

    Line-rewritten rather than re-serialised, so the example's comments — which
    are most of the file and the only documentation these settings have —
    survive into the thing an operator will actually read when a run surprises
    them.

    An example line the DEPLOYMENT does not state is not a deployment value:
    the example is a template for a new deployment, and production takes
    `nora/config.py`'s default for a key it never reads. Such a line is
    OMITTED, so the subject loads the same schema default production
    resolves — for a nullable default the omitted key computes to whatever
    the model derives (a `None` whose `prompt_window_effective` still lands
    on the deployment's number), and for a runtime-keyed container like
    `dreamer.budgets.<name>` the empty section is dropped too, so no
    template-only member enters the loaded dict. A key that survives into
    the generated file without the deployment stating it is a rewrite
    failure, and `divergences()` names it — resolved defaults are compared,
    `UNRESOLVED` ones refuse the run rather than reading as agreement.
    """
    take = adopted(deployment_toml)

    # A key the deployment SETS but the example never mentions has no line to
    # rewrite, and then silently falls back to the CODE default — which is not
    # the deployment's value. `store.prune_enabled` defaults to True in
    # `nora/config.py`, the deployment sets it False, and the example omits it:
    # a sandbox would prune its own seeded rows mid-run.
    want: dict[str, list[tuple[str, object]]] = {}
    for key, value in take.items():
        section, _, leaf = key.rpartition(".")
        want.setdefault(section, []).append((leaf, value))

    lines = example_toml.splitlines()
    declared = {
        line.strip().strip("[]") for line in lines
        if line.strip().startswith("[") and not line.strip().startswith("[[")
    } | {""}

    out: list[str] = []
    section = ""
    seen: set[str] = set()

    def close(current: str) -> None:
        """Emit the deployment's keys this section never mentioned, INSIDE it."""
        for leaf, value in want.get(current, []):
            full = f"{current}.{leaf}" if current else leaf
            if full in seen:
                continue
            # `.extend`, not `out +=`: an augmented assignment inside a nested
            # function binds `out` as ITS local, and this would raise
            # UnboundLocalError on the first section carrying a missing key.
            out.extend([
                "# SET BY THE DEPLOYMENT, absent from config.example.toml. Without",
                "# this line the sandbox takes nora/config.py's own default, which",
                "# is not the deployment's value.",
                f"{leaf} = {_fmt(value)}",
            ])
            seen.add(full)

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and not stripped.startswith("[["):
            close(section)
            out.append("")
            section = stripped.strip("[]")
        match = _KEY.match(line)
        if match and not stripped.startswith("#"):
            full = f"{section}.{match.group(2)}" if section else match.group(2)
            seen.add(full)
            if full in take:
                line = f"{match.group(1)}{match.group(2)}{match.group(3)}{_fmt(take[full])}"
            elif _exempt(full):
                pass
            elif (config_default(full) is UNRESOLVED
                    and not config_member_of_runtime_map(full)):
                # A required field the deployment never stated keeps the
                # example's line so the file loads; `divergences()` names
                # the unknown. A runtime-map member is omitted instead.
                pass
            else:
                continue
        out.append(line)
    close(section)

    for orphan in sorted(s for s in want if s not in declared):
        out += ["", f"[{orphan}]"]
        for leaf, value in want[orphan]:
            if f"{orphan}.{leaf}" not in seen:
                out.append(f"{leaf} = {_fmt(value)}")

    # A section whose every example line was omitted must not emit its
    # bare header either — an empty member is a template artifact.
    rendered: list[str] = []
    block: list[str] = []

    def flush() -> None:
        nonlocal block
        if block and (not block[0].strip().startswith("[")
                      or any(_KEY.match(ln) and not ln.strip().startswith("#")
                             for ln in block[1:])):
            rendered.extend(block)
        block = []

    for line in out:
        if line.strip().startswith("[") and not line.strip().startswith("[["):
            flush()
        block.append(line)
    flush()
    out = rendered

    return _HEADER + "\n".join(out) + "\n"


ALLOWED_TO_DIFFER: dict[str, str] = dict.fromkeys(
    FORCE_OFF, "measurement uses private sinks rather than production telemetry")


def divergences(generated_toml: str, deployment_toml: str) -> dict[str, tuple]:
    """Settings the generated config holds differently from the deployment.

    **Both directions are checked.**

    Iterating only what the DEPLOYMENT states is not enough: a key the example
    carries and the deployment does not is NOT "a code default for both". For
    every such example-stated, deployment-silent key the subject takes the
    EXAMPLE's value while production takes `nora/config.py`'s default. They
    agree only by curation — the very divergence this module exists to close,
    reached through the other half of the key space.

    So example-only keys are compared against the CODE DEFAULT, which is what
    production actually runs. A key whose default cannot be resolved is
    reported as unresolvable rather than skipped: "cannot check" is not
    "agrees".
    """
    generated = dict(_flat(tomllib.loads(generated_toml)))
    deployment = dict(_flat(tomllib.loads(deployment_toml)))
    out: dict[str, tuple] = {}

    def exempt(key: str) -> bool:
        return _exempt(key) or key in ALLOWED_TO_DIFFER

    for key, value in deployment.items():
        if not exempt(key) and generated.get(key) != value:
            out[key] = (generated.get(key), value)

    for key, value in generated.items():
        if key in deployment or exempt(key):
            continue
        default = config_default(key)
        if default is UNRESOLVED:
            # A generated key whose production default cannot be established
            # is a refusal, never "cannot check, so agree".
            out[key] = (value, UNRESOLVED)
            continue
        # A `Path` default against the TOML's string is the same value in two
        # types, not a divergence.
        if isinstance(default, Path):
            if str(default) != str(value):
                out[key] = (value, str(default))
        elif value != default:
            out[key] = (value, default)
    return out


def materialise(*, example: Path, deployment: Path, target: Path) -> Path:
    """Write the eval config, freshly, for one run — or refuse.

    Overwrites rather than reusing: a stale file is a run measuring a Nora the
    deployment has since stopped being, and nothing would say so.

    **Verified against the deployment before it is used, because this is the
    one place both facts are in hand.** Whether every setting matches is a
    property of the DEPLOYMENT, not of `build()`, so a unit test could only
    check its own fixture. Here it is checkable for real — and fail-closed, so
    a rewrite rule that silently stops applying refuses the run instead of
    producing a number measured against a Nora nobody deployed.
    """
    deployment_toml = deployment.read_text(encoding="utf-8")
    generated = build(example.read_text(encoding="utf-8"), deployment_toml)

    drift = divergences(generated, deployment_toml)
    if drift:
        named = "\n".join(
            f"  {key}: generated={got!r}  deployment={want!r}"
            for key, (got, want) in sorted(drift.items())
        )
        raise ValueError(
            f"the generated eval config differs from the deployment in "
            f"{len(drift)} setting(s):\n{named}\n\n"
            "An eval subject composed from this would measure a Nora that is "
            "not deployed. Either the rewrite failed, or the setting belongs "
            "in ALLOWED_TO_DIFFER with the reason it is not a behaviour "
            "difference."
        )

    target.write_text(generated, encoding="utf-8")
    return target


_HEADER = """\
# Generated evaluation configuration; private and never committed.
# Functional settings come from the declared deployment. External endpoints
# are bound to case-owned services by nora.subject.obtain.
# See docs/engineering/evaluation-subject.md.
"""


def load_overrides(path: Path) -> tuple[dict, str]:
    """Validate and fingerprint the same operator-supplied candidate bytes."""
    from nora.subject.obtain import validate_subject_overrides

    raw = path.read_bytes()
    parsed = json.loads(raw.decode())
    if not isinstance(parsed, dict) or not all(
            isinstance(key, str) and key and not key.startswith(".") for key in parsed):
        raise ValueError("--subject-overrides must be a JSON object of dotted keys")
    validate_subject_overrides(parsed)
    return parsed, hashlib.sha256(raw).hexdigest()
