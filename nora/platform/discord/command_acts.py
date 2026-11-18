"""Discord command mutations issued on a gateway or interaction event."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from functools import wraps
from inspect import signature
from typing import Any, TypeVar

from nora.memory import ActAuthority, ActEnvelope, ActInvocation, Outcome, act_event, declare_act

T = TypeVar("T")
_REPLY_ORDINAL = "nora.act.command_reply.ordinal"


def command_event(command):
    @wraps(command)
    async def run(owner, interaction, *args, **kwargs):
        async with owner._command_acts.event(interaction):
            return await command(owner, interaction, *args, **kwargs)

    run.__signature__ = signature(command, eval_str=True)
    return run


class DiscordCommandActs:
    def __init__(
        self, envelope: ActEnvelope | None = None,
        room_unit: Callable[[str], str | None] | None = None,
        person_id: Callable[[str], Awaitable[str | None]] | None = None,
    ) -> None:
        self._envelope = envelope
        self._room_unit = room_unit
        self._person_id = person_id

    async def _execute(
        self, executor: str, destination: str, event: str, authority: ActAuthority,
        call: Callable[[], Awaitable[T]], *, interaction: Any = None,
    ) -> T | None:
        if self._envelope is None:
            return await call()

        ordinal = 0
        if interaction is not None:
            ordinal = interaction.extras.get(_REPLY_ORDINAL, 0)
            interaction.extras[_REPLY_ORDINAL] = ordinal + 1
        act = declare_act(
            executor, destination, event=event, authority=authority, from_pass=False,
            ordinal=ordinal,
        )

        async def invoke(_key: str | None) -> ActInvocation[T]:
            return ActInvocation(await call(), Outcome("done"))

        result = await self._envelope.invoke(act, invoke)
        return result.value if result is not None else None

    async def _authority(self, interaction: Any, room: str | None) -> ActAuthority:
        unit = self._room_unit(room) if self._room_unit is not None and room else None
        person = (
            await self._person_id(str(interaction.user.id))
            if unit is not None and self._person_id is not None else None
        )
        return ActAuthority(unit, person, room=room)

    @staticmethod
    def _room(interaction: Any) -> str | None:
        guild = getattr(interaction, "guild_id", None)
        channel = getattr(interaction, "channel", None)
        channel_id = getattr(channel, "parent_id", None) or getattr(interaction, "channel_id", None)
        return f"{guild}/{channel_id}" if guild and channel_id else None

    @asynccontextmanager
    async def event(self, interaction: Any):
        with act_event(
            str(getattr(interaction, "id", "") or ""),
            await self._authority(interaction, self._room(interaction)),
        ):
            yield

    async def denied(self, interaction: Any, line: str, *, room: str | None = None) -> None:
        await self._execute(
            "discord.command_denial", f"discord:{room or 'dm'}",
            str(getattr(interaction, "id", "") or ""), await self._authority(interaction, room),
            lambda: interaction.response.send_message(line, ephemeral=True),
        )

    async def respond(self, interaction: Any, op: str, *args: Any, **kwargs: Any) -> None:
        room = self._room(interaction)
        call = {
            "reply": lambda: interaction.response.send_message(*args, **kwargs),
            "defer": lambda: interaction.response.defer(*args, **kwargs),
            "followup": lambda: interaction.followup.send(*args, **kwargs),
            "edit": lambda: interaction.response.edit_message(*args, **kwargs),
        }[op]
        await self._execute(
            "discord.command_reply", f"discord:{room or 'dm'}",
            str(getattr(interaction, "id", "") or ""), await self._authority(interaction, room),
            call, interaction=interaction,
        )

    async def sync(
        self, tree: Any, gateway_session_id: str, application_id: str,
    ) -> None:
        await self._execute(
            "discord.command_sync", f"discord-commands:{application_id}", gateway_session_id,
            ActAuthority(None), lambda: tree.sync(),
        )

    async def timeout(self, message: Any, interaction: Any, view: Any) -> None:
        guild, channel = getattr(message, "guild", None), getattr(message, "channel", None)
        room = f"{guild.id}/{channel.id}" if guild is not None and channel is not None else None
        await self._execute(
            "discord.command_reply", f"discord:{room or 'dm'}",
            str(getattr(interaction, "id", "") or ""), await self._authority(interaction, room),
            lambda: message.edit(view=view), interaction=interaction,
        )

    async def notice(
        self, channel: Any, call: Callable[[], Awaitable[str]],
        *, event: str, room: str | None, ordinal: int,
    ) -> str:
        if self._envelope is None:
            return await call()
        unit = self._room_unit(room) if room and self._room_unit is not None else None
        act = declare_act(
            "discord.music_notice", f"discord-channel:{channel.id}", event=event,
            authority=ActAuthority(unit, room=room), ordinal=ordinal, from_pass=False,
        )
        return (await self._envelope.perform(
            act, call, lambda status: Outcome(
                "done" if status == "sent" else "failed" if status == "refused" else "uncertain"
            ),
        )) or "unknown"
