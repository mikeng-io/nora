"""Host-local private Compose projection and migration; no values on stdout."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import tempfile
import tomllib
from pathlib import Path

NATIVE_CREDENTIALS = {
    "NORA_BRIDGE_TOKEN", "NORA_BRIDGE_API_TOKEN", "POSTGRES_PASSWORD",
    "RUSTFS_ROOT_USER", "RUSTFS_ROOT_PASSWORD",
    "NORA_REDIS_PRODUCER_PASSWORD", "NORA_REDIS_CONSUMER_PASSWORD",
    "NORA_REDIS_INSPECTOR_PASSWORD",
    "DISCORD_CLIENT_ID", "DISCORD_CLIENT_SECRET", "AUTH_SECRET", "DISCORD_BOT_TOKEN",
    "NORA_OTP_SECRET", "NORA_OTP_API_TOKEN", "NORA_COMPUTER_TOKEN", "MEDIA_ENCRYPTION_KEY",
    "XAI_OAUTH_REFRESH_TOKEN", "OPENAI_CODEX_REFRESH_TOKEN", "GIPHY_API_KEY", "PULSE_API_TOKEN",
    "PULSE_MCP_TOKEN",
}


def dotenv(path: Path) -> dict[str, str]:
    values = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:]
            key, sep, raw = line.partition("=")
            if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                raise ValueError("unsupported dotenv assignment; inspect privately")
            if raw.lstrip().startswith(("'", '"')):
                words = shlex.split(raw, comments=True)
            else:
                words = [raw.split(" #", 1)[0].strip()] if raw.strip() else []
            if len(words) > 1 or "${" in raw:
                raise ValueError("unsupported dotenv syntax; inspect privately")
            values[key] = words[0] if words else ""
    return values


def source(runtime: Path, environ: dict[str, str] | None = None) -> dict[str, str]:
    secret = runtime / "secret.toml"
    data = tomllib.loads(secret.read_text()) if secret.exists() else {}
    if set(data) - {"credentials"}:
        raise ValueError("invalid secret schema")
    credentials = data.get("credentials", {})
    if not isinstance(credentials, dict) or any(
        not isinstance(v, str) for v in credentials.values()
    ):
        raise ValueError("invalid credential values")
    return {
        **credentials,
        **dotenv(runtime / ".env"),
        **(os.environ if environ is None else environ),
    }


def references(tree: dict) -> set[str]:
    result: set[str] = set()
    for key, value in tree.items():
        if isinstance(value, dict):
            result.update(references(value))
        elif key.endswith("_env") and isinstance(value, str) and value:
            result.add(value)
    return result


def projection(config: dict, values: dict[str, str]) -> dict:
    """Extra operator aliases only; existing Compose owns native image mappings."""
    presence = config.get("presence", {})
    login_fields = {"client_credential_env", "username_env", "password_env", "auth_token_env"}
    scopes = {
        "nora": references({k: v for k, v in config.items() if k != "presence"})
        | references({k: v for k, v in presence.items() if k not in login_fields}),
        "presence": references({k: config[k] for k in ("store", "obs") if k in config})
        | references({k: v for k, v in presence.items() if k in login_fields}),
        "analytics": references({k: config[k] for k in ("store", "obs") if k in config}),
    }
    services = {
        service: {"environment": {k: values[k] for k in sorted(keys) if k in values}}
        for service, keys in scopes.items()
    }
    native = {
        "app": {"NORA_OTP_API_TOKEN": config.get("otp", {}).get("api_token_env", "")},
        "voice-sidecar": {"DISCORD_BOT_TOKEN": config.get("platforms", {}).get(
            "discord", {}).get("token_env", "")},
    }
    for service, aliases in native.items():
        projected = {target: values[ref] for target, ref in aliases.items() if ref in values}
        if projected:
            services[service] = {"environment": projected}
    return {"services": services}



def private_write(path: Path, body: str) -> None:
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".delivery-")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def prepare(runtime: Path, environ: dict[str, str] | None = None,
            *, output_directory: Path | None = None) -> list[str]:
    config_path = runtime / "config.toml"
    config = tomllib.loads(config_path.read_text()) if config_path.exists() else {}
    from urllib.parse import urlsplit

    tracing = config.get("obs", {}).get("tracing", {})
    signoz = tracing.get("enabled", False) and urlsplit(
        tracing.get("endpoint", "")).hostname == "signoz-ingester"
    signoz_overlay = runtime / "deploy/signoz/docker-compose.signoz.yaml"
    if signoz and not signoz_overlay.is_file():
        raise FileNotFoundError("install the SigNoz Compose overlay before enabling its endpoint")
    if not (runtime / "secret.toml").exists() and not references(config) and not signoz:
        return []
    values = source(runtime, environ)
    config = tomllib.loads((runtime / "config.toml").read_text())
    file_keys = set(dotenv(runtime / ".env"))
    secret_path = runtime / "secret.toml"
    if secret_path.exists():
        file_keys.update(tomllib.loads(secret_path.read_text()).get("credentials", {}))
    file_keys.update(references(config))
    values = {k: v for k, v in values.items() if k in file_keys}
    directory = output_directory if output_directory is not None else runtime / ".secret-delivery"
    directory.mkdir(mode=0o700, exist_ok=True)
    directory.chmod(0o700)
    envfile = directory / "compose.env"
    # Single quotes prevent Compose interpolation of credential dollars.
    if any("\n" in v or "\r" in v or "'" in v for v in values.values()):
        raise ValueError("unsupported Compose credential encoding; inspect privately")
    private_write(envfile, "".join(f"{k}='{v}'\n" for k, v in sorted(values.items())))
    overlay = directory / "compose.json"
    projected = projection(config, values)
    # Compose performs interpolation in YAML/JSON values as well.
    private_write(overlay, json.dumps(projected).replace("$", "$$"))
    arguments = ["--env-file", str(envfile), "-f", str(overlay)]
    if signoz:
        arguments.extend(["-f", str(signoz_overlay)])
    return arguments


def migrate(runtime: Path, *, apply: bool) -> None:
    target = runtime / "secret.toml"
    existing = tomllib.loads(target.read_text()).get("credentials", {}) if target.exists() else {}
    config_path = runtime / "config.toml"
    config = tomllib.loads(config_path.read_text()) if config_path.exists() else {}
    credential_names = NATIVE_CREDENTIALS | references(config) | set(existing)
    values = {k: v for k, v in dotenv(runtime / ".env").items() if k in credential_names}
    # Preserve an existing secret entry; differing env overrides need operator reconciliation.
    if any(k in existing and existing[k] != v for k, v in values.items()):
        raise ValueError("environment and secret entries differ; reconcile privately")
    merged = {**values, **existing}
    body = "[credentials]\n" + "".join(
        f"{k} = {json.dumps(v)}\n" for k, v in sorted(merged.items())
    )
    tomllib.loads(body)
    if not apply:
        print("VALIDATED: migration candidate; no files changed")
        return
    backup = Path(tempfile.mkdtemp(prefix="secret-migration-", dir=runtime))
    backup.chmod(0o700)
    for name in (".env", "secret.toml", "config.toml"):
        if (runtime / name).exists():
            shutil.copy2(runtime / name, backup / name)
            (backup / name).chmod(0o600)
    # OAuth state is not changed; backup the complete existing state directory.
    if (runtime / "state").exists():
        shutil.copytree(runtime / "state", backup / "state", symlinks=True)
    private_write(target, body)
    print("APPLIED: private backup and secret.toml written; legacy .env retained")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--compose-exec", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.compose_exec == []:
        parser.error("--compose-exec requires exec arguments")
    try:
        runtime = args.runtime_root.resolve()
        if args.compose_exec is not None:
            values = source(runtime)
            os.chdir(runtime)
            os.execvpe(  # noqa: S606 -- replace helper; preserve stdin and exit status
                "docker",  # noqa: S607 -- host Docker CLI resolved through PATH
                ["docker", "compose", "exec", *args.compose_exec], values)
        migrate(runtime, apply=args.apply)
    except (ValueError, OSError):
        raise SystemExit("FAILED: private migration validation; values withheld") from None


if __name__ == "__main__":
    main()
