"""Command: python -m nora.memory.adapters.register_stored --config PATH."""

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine

from nora import config
from nora.access import EmptyPersons
from nora.app.composition_stores import managed_identity
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.item_registration import RegistrationScope
from nora.memory.adapters.stored_row_registration import SqlStoredRows
from nora.memory.app.register_stored_rows import RegisterStoredRows, TableRegistration
from nora.persons import Persons, load_persons
from nora.platform.discord.channels import StaticChannelDirectory
from nora.policy import Policy, load_policy


def _directory(path: Path | None) -> StaticChannelDirectory:
    if path is None:
        return StaticChannelDirectory()
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit("unavailable: could not read channel-directory JSON") from exc
    if not isinstance(snapshot, dict) or not all(
        guild and isinstance(channels, dict)
        and all(channel and isinstance(name, str) for channel, name in channels.items())
        for guild, channels in snapshot.items()
    ):
        raise SystemExit(
            "unavailable: channel-directory must map guild IDs to channel ID/name maps",
        )
    return StaticChannelDirectory(channels=snapshot)


def _registration_policy(policy_path: Path, channel_directory: Path | None) -> Policy:
    directory = _directory(channel_directory)
    policy = load_policy(policy_path, channels=directory)
    missing = sorted({
        conversation.guild
        for conversation in policy.conversations
        if conversation.guild and directory.guild_channels(guild_id=conversation.guild) is None
    })
    if missing:
        names = ", ".join(missing)
        raise SystemExit(f"unavailable: Discord channel-directory missing guilds {names}")
    return policy


async def register_with_policy(
    engine: AsyncEngine, *, policy_path: Path, persons: Persons, channel_directory: Path | None,
) -> dict[str, TableRegistration]:
    policy = _registration_policy(policy_path, channel_directory)
    return await RegisterStoredRows(SqlStoredRows(engine, RegistrationScope(policy, persons))).run()


async def _register(path: Path, channel_directory: Path | None) -> None:
    cfg = config.load(path, require_secrets=False)
    url = cfg.credentials.get(cfg.store.database_url_env)
    if not url:
        raise SystemExit(f"Postgres URL is missing ({cfg.store.database_url_env})")
    engine = create_sql_engine(url)
    try:
        if cfg.identity.backend == "managed":
            policy = _registration_policy(cfg.policy_file, channel_directory)
            managed = await managed_identity(cfg, EmptyPersons(), policy, engine)
            try:
                scope = RegistrationScope(policy, None, access=managed.services.access)
                counts = await RegisterStoredRows(SqlStoredRows(engine, scope)).run()
            finally:
                if managed.session is not None:
                    await managed.session.close()
        else:
            counts = await register_with_policy(
                engine, policy_path=cfg.policy_file, persons=load_persons(cfg.identity_file),
                channel_directory=channel_directory,
            )
    finally:
        await engine.dispose()
    for family, report in counts.items():
        print(
            f"{family} registered={report.registered} unscoped={report.unscoped} "
            f"covered={report.covered}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Register existing rows after the graph migration")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument(
        "--channel-directory", type=Path, metavar="JSON",
        help='Offline Discord room-channel snapshot, {"guild_id": {"channel_id": "name"}}. '
             'Required for every guild in policy.',
    )
    args = parser.parse_args()
    asyncio.run(_register(args.config, args.channel_directory))


if __name__ == "__main__":
    main()
