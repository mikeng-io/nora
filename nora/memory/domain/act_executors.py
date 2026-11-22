"""The closed registry of outward executors."""

from dataclasses import dataclass
from types import MappingProxyType

from nora.domain.lineage import split_room
from nora.memory.domain.acts import ActKind, Guarantee


@dataclass(frozen=True, slots=True)
class Executor:
    kind: ActKind
    guarantee: Guarantee


EXECUTORS = MappingProxyType({
    "computer.dispatch": Executor("delegation", "idempotent_effect"),
    "discord.command_denial": Executor("delivery", "unknown"),
    "discord.command_sync": Executor("effect", "unknown"),
    "discord.command_reply": Executor("delivery", "unknown"),
    "platform.reply": Executor("delivery", "unknown"),
    "platform.reaction": Executor("delivery", "unknown"),
    "platform.progress": Executor("delivery", "unknown"),
    "platform.otp": Executor("delivery", "unknown"),
    "signal.private_link": Executor("delivery", "unknown"),
    "document.write": Executor("effect", "unknown"),
    "bridge.delivery": Executor("delivery", "unknown"),
    "bridge.typing": Executor("effect", "unknown"),
    "voice.control": Executor("effect", "unknown"),
    "voice.speech": Executor("delivery", "unknown"),
    "music.queue": Executor("effect", "unknown"),
    "bridge.music": Executor("effect", "unknown"),
    "bridge.speech": Executor("delivery", "unknown"),
    "discord.music_notice": Executor("delivery", "unknown"),
    "subagent.launch": Executor("delegation", "unknown"),
    "computer.run": Executor("delegation", "unknown"),
    "computer.workspace": Executor("effect", "unknown"),
    "computer.lifecycle": Executor("effect", "unknown"),
    "browser.connect": Executor("delegation", "unknown"),
    "browser.mutation": Executor("effect", "unknown"),
    "browser.stop": Executor("effect", "unknown"),
    "mcp.invoke": Executor("effect", "unknown"),
})


def executor_health_platform(executor: str, destination: str) -> str | None:
    """Resolve an act to a recorded adapter-health source."""
    if executor not in EXECUTORS or not executor.startswith(("platform.", "discord.", "signal.")):
        return None
    if executor.startswith("discord."):
        prefix, _, target = destination.partition(":")
        if target and prefix in {"discord-commands", "discord-channel"}:
            return "discord"
    return split_room(destination)[0]
