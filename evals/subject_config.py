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

import re
import tomllib
from pathlib import Path

from nora.subject import config_default

#: Deployment settings the sandbox must NOT inherit. None is a behaviour
#: difference; each would reach OUT of the sandbox or point at an endpoint that
#: only resolves inside the deploy network.
SKIP_PREFIX: tuple[str, ...] = (
    "obs.",                    # a run's spans/errors would land in the deployment's collector
    "analytics.",              # a run's counters would move real dashboards
    "heartbeat.",              # nothing in an eval lives long enough to heartbeat
    "presence.",               # a live location feed no anchor can reconstruct
    # NOT `platforms.` wholesale. Only the credentials and endpoints: an
    # adapter that is ENABLED is part of the turn, because the registry's
    # `self_aliases()` is what feeds `self_ids`
    # (`nora/app/composition.py`) and `self_ids` is what
    # `MentionPolicy`/`ReplyToNoraPolicy` match on. With the deployment's text
    # trigger off, mention-and-reply IS how she is engaged, so a disabled adapter
    # removes the live engagement path.
    #
    # Switching every adapter off raises
    # `StartupError: no platform adapter is enabled`.
    # `_baseline()` neutralises the network by pointing
    # `platforms.signal.account` at a placeholder, which is the right shape —
    # enabled like the deployment. That stops the startup lookup only; a SEND is
    # landed in the sandbox by `nora.subject.sandbox_signal`.
    "platforms.signal.account",
    "platforms.signal.base_url",
    "platforms.discord.token_env",
    "platforms.whatsapp.base_url",
    "media.rustfs.",           # endpoint resolves only inside the deploy network
    "evals.langfuse.",         # a run would write its own traces back into production's
    "store.data_dir",          # `_baseline()` points it at the case's own workdir
    "social.bridge_url",
    "speech.tts.sidecars",
)

#: Forced OFF whatever either file says. Inheriting a value would leave
#: `platforms.signal.enabled` true: the deployment has it on, the example has
#: it on, and neither is what a sandbox wants.
FORCE_OFF: tuple[str, ...] = (
    # A DIVERGENCE, declared rather than hidden. Signal's adapter is neutralised
    # by a placeholder account and stays ENABLED like the deployment. Discord's
    # cannot be: it resolves its own identity at startup with a live
    # `GET /users/@me` (`nora/platform/discord/self_id.py`), so a placeholder
    # token is HTTP 401 and a real one means every eval run authenticates
    # against the deployment's actual bot.
    #
    # THE COST: the registry's `self_aliases()` feeds
    # `self_ids`, which `MentionPolicy`/`ReplyToNoraPolicy` match on. With
    # Discord off, a Discord room's mention-and-reply engagement cannot fire —
    # and that IS the deployment's engagement path while its text trigger is off.
    # No corpus exercises it, so nothing is measurably wrong; a mention/reply
    # corpus on Discord is blocked on closing this.
    "platforms.discord.enabled",
    "platforms.whatsapp.enabled",
    "obs.tracing.enabled", "obs.errors.enabled",
    "analytics.enabled", "heartbeat.enabled", "presence.enabled",
    "media.rustfs.enabled", "evals.langfuse.enabled",
    # The deferred-delivery poller. Off by default: with no backend registered
    # it is a loop with nothing behind it, and off spares the "enabled but no
    # backend" boot warning. NOT forced off when the deployment runs the
    # sub-agent (`forced_off`): `[subagent] enabled` REFUSES to load without
    # `[compute] enabled`, so forcing it off would make every such subject unloadable.
    "compute.enabled",
)

#: Switched back ON, for a FULL-RESTORE sandbox only (`evals.scenarios.
#: FULL_RESTORE_CORPORA`, seeded by `nora.subject.sandbox_as_of`). Applied as a
#: subject override by `evals.backbone._drive_draw`, never written into the
#: generated config, so every other corpus composes exactly as before.
#:
#: `presence.enabled` is forced off above because a snapshot-seeded sandbox holds
#: no presence rows: its reason was «a live location feed no anchor can
#: reconstruct». A full restore DOES reconstruct them: `presence_fixes`,
#: `presence_stays`/`_trips`/`_places` and the `rollup_*` builds are restored as
#: of the instant (`nora.infra.sql.history.HISTORY_TABLES`).
#:
#: WHAT THE FLAG DOES IN THE NORA PROCESS, all of it (`nora/app/composition.py`):
#:
#: - wires the READER, `PresenceStore` and the circles that `where_is`,
#:   `where_been` and `routine_read` read through;
#: - spawns `_presence_preflight` as a background task (the R-21/R-31
#:   boot alarm), which reads the roster of EVERY policy conversation whose
#:   platform has a roster. Checked, not neutralised: the rosters are exactly
#:   the ones composition already builds for every corpus. Signal's
#:   dials `platforms.signal.base_url`, the example's loopback
#:   `127.0.0.1:18080` (`SKIP_PREFIX`); Discord and WhatsApp are forced off, so
#:   they have none; Instagram's dials the deployment's compose-private
#:   `instagram-bridge:18082`, which `nora/platform/registry.py` already
#:   dials at composition for every corpus. Every read is a GET of a member
#:   list; it writes nothing and adds no endpoint;
#: - and, with a store present, lets `where_is` ask `presence.refresh_url` for a
#:   fresh fix, which stays the example's empty value here (`presence.` is in
#:   `SKIP_PREFIX`), so nothing is asked.
#:
#: The COLLECTOR, which polls Life360, is a separate process (`python -m
#: nora.presence`, `nora/presence/composition.py`) that `obtain()` never
#: composes, so NOTHING POLLS.
#:
#: THE GEOCODER IS NOT THE FLAG'S. `reverse_geocoder` (composition.py) builds a
#: billed Google (or HK-gov) client from the process environment whenever the
#: key is set, flag on or off. With the flag off nothing can hand
#: `reverse_geocode` a coordinate; with the reader on, `where_is`/`where_been`
#: can, and the only thing between them and a billed call would be the room
#: reading as degraded (below), which a roster fix would remove. So the key's
#: env var is pointed at a name nothing sets: the full-restore subject holds no
#: geocoder, and `reverse_geocode` answers its own `unavailable`.
#:
#: A DIVERGENCE THAT REMAINS, declared: the R-31 guard is production's and is not
#: touched (`_presence_audience`, `Whereis.locate`, the policy's `location`
#: flag). It needs the room's live roster, and the sandbox's Signal roster dials
#: no daemon (`obtain._baseline`'s placeholder account), so `room.scope()` reads
#: the room as DEGRADED and every presence read answers `unavailable` («I can't
#: confirm who is in this group right now») — production's own answer for an
#: unreadable roster. The rows are restored and reachable by the reader; what
#: is not reconstructed is who was in the room at the instant, which is
#: recorded nowhere.
FULL_RESTORE_OVERRIDES: dict[str, object] = {
    "presence.enabled": True,
    "presence.geocode_api_key_env": "NORA_EVAL_NO_GEOCODER",
    "presence.hkgov_token_env": "NORA_EVAL_NO_GEOCODER",
}

#: The env var `FULL_RESTORE_OVERRIDES` names for every geocoder credential.
#: `obtain` never sets it, and a subject composed while something has refuses
#: in `evals.backbone._drive_draw` rather than build a billed client.
NO_GEOCODER_ENV = "NORA_EVAL_NO_GEOCODER"

#: What `[subagent] enabled` needs ON to load (`nora/config.py`
#: `_subagent_needs_the_dreamer_and_the_poller`); a key here leaves `FORCE_OFF`
#: when the deployment runs the sub-agent. The eval subject then registers a
#: recorder as the worker (`nora/subject/recording_subagent.py`), so the poller
#: has nothing to deliver and no worker model call is made.
SUBAGENT_PRECONDITIONS: tuple[str, ...] = ("compute.enabled",)


def forced_off(values: dict[str, object]) -> tuple[str, ...]:
    """`FORCE_OFF`, less what the deployment's sub-agent needs left alone."""
    if values.get("subagent.enabled") is True:
        return tuple(k for k in FORCE_OFF if k not in SUBAGENT_PRECONDITIONS)
    return FORCE_OFF

_KEY = re.compile(r"^(\s*)([A-Za-z_][\w-]*)(\s*=\s*)(.*)$")


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
    taken.update(dict.fromkeys(forced_off(values), False))
    return taken


def build(example_toml: str, deployment_toml: str) -> str:
    """`config.example.toml` with the deployment's behaviour values over it.

    Line-rewritten rather than re-serialised, so the example's comments — which
    are most of the file and the only documentation these settings have —
    survive into the thing an operator will actually read when a run surprises
    them.
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
        out.append(line)
    close(section)

    for orphan in sorted(s for s in want if s not in declared):
        out += ["", f"[{orphan}]"]
        for leaf, value in want[orphan]:
            if f"{orphan}.{leaf}" not in seen:
                out.append(f"{leaf} = {_fmt(value)}")

    return _HEADER + "\n".join(out) + "\n"


#: Switches the sandbox may hold differently from the deployment, each with the
#: reason it is not a behaviour difference. Anything else differing is refused.
#:
#: DECLARED, not merely tolerated: a setting that starts diverging must be
#: argued onto this list, not discovered months later having quietly never been
#: evaluated.
ALLOWED_TO_DIFFER: dict[str, str] = {
    "platforms.discord.enabled":
        "its adapter resolves identity with a live GET /users/@me, so it needs "
        "the deployment's real bot token; the cost is that Discord "
        "mention/reply engagement cannot fire in a sandbox",
    "platforms.whatsapp.enabled": "as above",
    "obs.tracing.enabled":
        "a run's spans would land in the deployment's collector beside real ones",
    "obs.errors.enabled": "a run's exceptions would page on a real Sentry project",
    "analytics.enabled": "a run's counters would move the deployment's dashboards",
    "heartbeat.enabled": "nothing in an eval is long-lived enough to heartbeat",
    "presence.enabled":
        "reads a live location feed no SNAPSHOT anchor can reconstruct; a "
        "full-restore sandbox turns the reader back on (FULL_RESTORE_OVERRIDES), "
        "never the collector",
    "media.rustfs.enabled": "its endpoint resolves only inside the deploy network",
    "evals.langfuse.enabled": "as above, and a run would write its own traces back",
    "compute.enabled":
        "off unless the deployment runs the sub-agent: with no backend the "
        "poller has nothing to poll. Under `[subagent] enabled` it is kept (the "
        "config will not load without it) and the worker is a recorder, so the "
        "poller still delivers nothing",
}


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
        return (
            any(key.startswith(prefix) for prefix in SKIP_PREFIX)
            or key in ALLOWED_TO_DIFFER
        )

    for key, value in deployment.items():
        if not exempt(key) and generated.get(key) != value:
            out[key] = (generated.get(key), value)

    for key, value in generated.items():
        if key in deployment or exempt(key):
            continue
        default = config_default(key)
        if default is None:
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
# NORA AS THE DEPLOYMENT RUNS HER — the config every eval subject composes.
#
# GENERATED PER RUN by `evals/subject_config.py`, from `config.example.toml`
# plus the deployment's own `config.toml`. Never edit; never commit. It is
# gitignored for the same reason `config.toml` is: it carries the deployment's
# real values, including the doctrine canary and room ids.
#
# WHY IT EXISTS. Until 2026-09-21 every eval subject was built from
# `config.example.toml` — a safe template for a FRESH deployment, so nearly
# everything ships off. 65 values differed, including the model, the reasoning
# effort, and the relevance gate, participation, standing, culture, notes, the
# Dreamer and most tools. The suite measured a different Nora, and no case
# could have caught it: the prompt was right, the reader was not.
# See `design/evaluation/01-boundaries.md` §5.
#
# WHAT IS DELIBERATELY NOT TAKEN, and why none is a behaviour difference:
#   platforms.*   the adapters would dial Signal/Discord; an eval drives the
#                 entry points directly and never listens.
#   obs.*, analytics.*, heartbeat.*, evals.langfuse.*
#                 a run's traces and counters would land in the deployment's
#                 own sinks, beside real ones.
#   media.rustfs.*, presence.*, social.bridge_url
#                 endpoints that resolve only inside the deploy network.
#   store.data_dir
#                 `_baseline()` points it at the case's own workdir.
# Anything else differing from the deployment is a DEFECT, and
# `tests/architecture/test_the_eval_subject_is_the_deployed_nora.py` says so.

"""
