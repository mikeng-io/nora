"""`nora config` — the operator's config mutation control plane.

The read-only CLI (`nora groups`, `nora persons pending`, ...) answers
"what is the state"; this module answers "change a setting" without a
hand-edit of a large TOML that is schema-validated (`extra = "forbid"` in
`nora.config.Config`) and easy to corrupt.

Deploy convention (infra/deploy-convention.md):

> Every app provides a CLI subcommand for config mutation. The subcommand
> validates the change against the schema, writes atomically (temp file +
> rename, so a crash never leaves a half-written config), and prints what
> changed.

Three properties are load-bearing, each enforced by something:

* **Comments survive.** `config.toml` carries hundreds of lines of
  rationale — the repo's convention is that a terse key is only safe
  because the comment explains *why*. `tomli` reads, `tomlkit` writes, so
  an edit never strips the prose that keeps the next operator honest.
  (This is why the write side is NOT `tomli_w` — it would drop every
  comment in the file.)
* **The write is atomic.** Same pattern as `cli._rewrite_env`:
  tmp + `os.replace`. A crash mid-write can never leave a
  half-written config behind; the old file survives until the rename.
* **The change is validated twice.** Before the write (the value must be a
  TOML literal) and after the write (the file must `config.load()` — the
  SAME loader the daemon uses, `extra = "forbid"` and all). A bad key, a
  wrong type, or a dangling reference makes `nora config set` fail closed
  and leaves the file untouched.

Mutation path discipline: the CLI prints the restart command but does NOT
restart the container — restart authority stays with the operator.
Secrets are never set through this CLI; they belong in `.env` (0600) and
are referenced by name (`token_env`).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import tempfile
from pathlib import Path

import tomlkit
from tomlkit.exceptions import TOMLKitError

from nora.config import Config, ConfigError, load, validate_schema

# ── deferred imports (circular-import break) ───────────────────────────────

def _cli_error(message: str) -> Exception:
    """Resolve `CliError` lazily — `cli.add_subcommands` imports this
    module, so importing back from `cli` at module top would be circular."""
    from nora.app.cli import CliError
    return CliError(message)


def _load_config(args: argparse.Namespace) -> Config:
    """Load the config for CLI use, without the deferred import cycle."""
    try:
        return load(Path(args.config), require_secrets=False)
    except ConfigError as exc:
        raise _cli_error(str(exc)) from exc


# ── dot-path navigation ──────────────────────────────────────────────────

def _navigate(
    doc: tomlkit.container.Container, dotted: str
) -> tuple[tomlkit.container.Container | dict[object, object], str]:
    """Walk `a.b.c` into `doc`, returning (container, leaf_key).

    Raises a CliError with a helpful message when an intermediate section
    does not exist — the operator sees "no [llm.foo] section" rather than
    a KeyError traceback.
    """
    parts = dotted.split(".")
    if not parts or not all(parts):
        raise _cli_error(f"bad dotted key: {dotted!r}")
    current = doc
    for part in parts[:-1]:
        if part not in current:
            path_so_far = ".".join(parts[: parts.index(part) + 1])
            raise _cli_error(f"no [{path_so_far}] section in {dotted!r}")
        current = current[part]
        if not isinstance(current, dict | tomlkit.container.Container):
            raise _cli_error(
                f"{'.'.join(parts[: parts.index(part) + 1])} is a scalar, not a table"
            )
    return current, parts[-1]


def _get_value(doc: tomlkit.container.Container, dotted: str) -> object:
    container, key = _navigate(doc, dotted)
    if key not in container:
        raise _cli_error(f"key {dotted!r} is not set")
    return container[key]


def _render_value(value: object) -> str:
    """Render a TOML value for `get` output.

    Strings print raw so `nora config get platforms.signal.account` shows
    the phone number without quotes; bools print lower-case TOML style;
    tables/arrays print as TOML.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, (dict, tomlkit.container.Container)):
        return tomlkit.dumps(value).strip()
    if isinstance(value, (list, tomlkit.items.Array)):
        return str(value)
    return str(value)


def _parse_value_expression(expression: str, current: object | None = None) -> object:
    """Parse a `set` value, using a type-compatibility rule.

    Rule: a TOML literal is honoured *only when its type matches the key's
    current value type* (or the key is new). Otherwise the expression stays
    a plain string.

    Why: `+85299998888` is a *valid TOML integer literal* — TOML allows a
    leading plus. Parsing it as an int would make `nora config set
    platforms.signal.account +85299998888` store an integer, and the
    schema (a string field) would reject it. The operator means a string;
    the only way to know is to compare against the key's current type.

    * current int -> `8` parses as int (match)  -> 8
    * current bool -> `false` parses as bool (match) -> False
    * current str -> `"+85299998888"` parses as str (match) -> phone
    * current str -> `+85299998888` parses as INT (mismatch) -> plain str
    * current None (new key) -> parse when possible, else plain str
    """
    try:
        parsed = tomlkit.parse(f"x = {expression}\n")
        value = parsed["x"]
    except (TOMLKitError, ValueError):
        return expression

    if current is None:
        return value
    if type(value) is type(current):
        return value
    # Explicit type mismatch: the operator typed a bare word for a string
    # key (e.g. a phone number) — keep it a string.
    return expression


# ── sync core (runs under asyncio.to_thread — same pattern as cli._rewrite_env) ──

def _read_doc(path: Path) -> tuple[str, tomlkit.container.Container]:
    """Read + parse, returning (old_text, doc) so a failed edit can
    restore the exact original bytes."""
    text = path.read_text(encoding="utf-8")
    return text, tomlkit.parse(text)


def _atomic_write(path: Path, text: str) -> None:
    """tmp + os.replace, preserving the original mode — same pattern as
    `cli._rewrite_env`. A crash between write and rename leaves the
    old file intact.

    Security: the temp is created by `mkstemp`
    — O_EXCL + random name in the same directory — so a predictable
    `.tmp` sibling cannot be pre-seeded as a symlink, and the mode is
    applied before content is written so a 0600 config is never visible
    as 0644 even briefly.
    """
    mode = path.stat().st_mode & 0o777
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=path.name + ".", suffix=".tmp"
    )
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _cmd_get(args: argparse.Namespace) -> int:
    _load_config(args)  # fail fast if the file is already broken
    path = Path(args.config)
    _text, doc = _read_doc(path)
    value = _get_value(doc, args.key)
    print(_render_value(value))
    return 0


def _validate_candidate(path: Path, doc: tomlkit.container.Container) -> None:
    """Validate the MUTATED document against the schema BEFORE any write.

    The daemon's own config.load() opens the file from disk, so to check
    the in-memory doc we serialise to a temp sibling, validate it, and
    delete the temp. This closes the SIGKILL/OOM window that a
    write-then-rollback design leaves open: the on-disk config is never
    invalid, not even for a moment.
    """
    mode = path.stat().st_mode & 0o777
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=path.name + ".", suffix=".candidate"
    )
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(tomlkit.dumps(doc))
        load(tmp, require_secrets=False)
    except ConfigError as exc:
        raise _cli_error(f"change rejected: {exc}") from exc
    except OSError:
        raise _cli_error("config: candidate_unreadable") from None
    finally:
        tmp.unlink(missing_ok=True)


# Field names that reference `.env` variables by name — secrets live in
# .env, never in the config file, and the CLI must not let the operator
# paste a secret value into them or echo one back.
_SECRET_ENV_SUFFIXES = ("_env",)


def _is_secret_env_key(dotted: str) -> bool:
    return dotted.lower().endswith(_SECRET_ENV_SUFFIXES)


def _render_display(value: object, secret: bool = False) -> str:
    """Render a value for operator output; redact when the key names an env
    reference (the field must hold an env-var NAME, not a secret VALUE)."""
    if secret:
        return "<redacted — set in .env, not via config CLI>"
    return _render_value(value)


def _cmd_set(args: argparse.Namespace) -> int:
    _load_config(args)
    path = Path(args.config)
    old_text, doc = _read_doc(path)

    container, key = _navigate(doc, args.key)
    old = container.get(key)
    secret = _is_secret_env_key(args.key)
    value = _parse_value_expression(args.value, current=old)
    if secret and (
        not isinstance(value, str)
        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value)
    ):
        # `*_env` fields hold the NAME of an environment variable (DEC-8:
        # secrets live in .env, referenced by name). Accept only a valid
        # env-var name — never a secret-looking literal.
        raise _cli_error(
            f"{args.key} is an env-var NAME field — value must be a valid "
            "environment variable name, e.g. OMNIROUTE_API_KEY (the secret "
            "itself lives in .env)"
        )
    container[key] = value

    _validate_candidate(path, doc)

    _atomic_write(path, tomlkit.dumps(doc))
    try:
        load(path, require_secrets=False)
    except ConfigError as exc:
        # The file is invalid — restore the old bytes so a failed edit
        # never leaves the operator with a config the daemon refuses.
        _atomic_write(path, old_text)
        raise _cli_error(f"change rejected: invalid resulting config: {exc}") from exc

    if old is None:
        print(f"set {args.key} = {_render_display(value, secret=secret)}")
    else:
        print(
            f"{args.key}: {_render_display(old, secret=secret)} -> "
            f"{_render_display(value, secret=secret)}"
        )
    print("restart to apply: docker compose up -d --force-recreate nora")
    return 0


def _cmd_unset(args: argparse.Namespace) -> int:
    _load_config(args)
    path = Path(args.config)
    old_text, doc = _read_doc(path)

    container, key = _navigate(doc, args.key)
    if key not in container:
        raise _cli_error(f"key {args.key!r} is not set")
    old = container.pop(key)

    _validate_candidate(path, doc)

    _atomic_write(path, tomlkit.dumps(doc))
    try:
        load(path, require_secrets=False)
    except ConfigError as exc:
        _atomic_write(path, old_text)
        raise _cli_error(f"change rejected: invalid resulting config: {exc}") from exc

    print(
        f"unset {args.key} "
        f"(was {_render_display(old, secret=_is_secret_env_key(args.key))})"
    )
    print("restart to apply: docker compose up -d --force-recreate nora")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        if getattr(args, "schema_only", False):
            validate_schema(Path(args.config))
            print("schema valid; runtime env unresolved")
        else:
            load(Path(args.config), require_secrets=False)
            print("config: valid")
    except ConfigError as exc:
        raise _cli_error(str(exc)) from None
    return 0


def _cmd_path(args: argparse.Namespace) -> int:
    print(str(Path(args.config).resolve()))
    return 0


# ── async wrappers (the CLI dispatcher awaits args.func; file I/O stays in
#    to_thread so the event loop never blocks — mirror of cli._rewrite_env) ──

async def cmd_config_get(args: argparse.Namespace) -> int:
    return await asyncio.to_thread(_cmd_get, args)


async def cmd_config_set(args: argparse.Namespace) -> int:
    return await asyncio.to_thread(_cmd_set, args)


async def cmd_config_unset(args: argparse.Namespace) -> int:
    return await asyncio.to_thread(_cmd_unset, args)


async def cmd_config_validate(args: argparse.Namespace) -> int:
    return await asyncio.to_thread(_cmd_validate, args)


async def cmd_config_path(args: argparse.Namespace) -> int:
    return await asyncio.to_thread(_cmd_path, args)


# ── parser wiring ────────────────────────────────────────────────────────

def add_config_subcommands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    parent: argparse.ArgumentParser,
) -> None:
    """Register `config` on the top-level parser (called from cli.add_subcommands)."""
    config = subparsers.add_parser(
        "config", parents=[parent], help="read and mutate config.toml safely",
    )
    config_sub = config.add_subparsers(dest="config_command", required=True)

    get = config_sub.add_parser("get", parents=[parent], help="print one setting")
    get.add_argument("key", help="dotted key, e.g. llm.budgets.web_fetch")
    get.set_defaults(func=cmd_config_get)

    set_ = config_sub.add_parser(
        "set", parents=[parent], help="set one setting atomically",
    )
    set_.add_argument("key", help="dotted key, e.g. llm.budgets.web_fetch")
    set_.add_argument("value", help="TOML literal or plain string")
    set_.set_defaults(func=cmd_config_set)

    unset = config_sub.add_parser(
        "unset", parents=[parent], help="remove one key",
    )
    unset.add_argument("key", help="dotted key to remove")
    unset.set_defaults(func=cmd_config_unset)

    validate = config_sub.add_parser(
        "validate", parents=[parent],
        help="check the file parses and passes the schema",
    )
    validate.add_argument("--schema-only", action="store_true",
                          help="check source schema only; does not validate runtime env")
    validate.set_defaults(func=cmd_config_validate)

    path = config_sub.add_parser(
        "path", parents=[parent], help="print the resolved config path",
    )
    path.set_defaults(func=cmd_config_path)
