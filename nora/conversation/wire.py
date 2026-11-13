import logging
from dataclasses import dataclass
from typing import Any

from nora.agent.relay import Relay
from nora.app.composition_helpers import _reasoning_effort
from nora.app.fire import relay_occasion
from nora.app.raw_clients import _DebriefModelClient
from nora.conversation.adapters.tools import build as _build
from nora.domain.quote_window import QuoteWindow

"""Compose this area's tool adapters from shared dependencies."""


def tool_specs(deps):
    return _build(deps)


log = logging.getLogger("nora.app.composition")


@dataclass(frozen=True)
class QuoteWiring:
    quote_window: Any


def quote(audit, cfg, metrics) -> QuoteWiring:
    # ONE quote-aware window resolver for the whole process (DEC-195), handed to every
    # transcript read that can grow past `context_window` to resolve a reply's quote: the
    # tag assembler AND the fire path's `build_context` — the same `MediaJoin` precedent…
    quote_window = QuoteWindow(
        audit=audit,
        metrics=metrics,
        quote_margin=cfg.store.quote_margin,
        quote_window_multiplier=cfg.store.quote_window_multiplier,
    )
    return QuoteWiring(quote_window=quote_window)


@dataclass(frozen=True)
class ReactionsWiring:
    giphy_client: Any
    reaction_picker: Any


def reactions(cfg, content_checks, doctrine_store, metrics) -> ReactionsWiring:
    giphy_client = None
    if giphy_key := cfg.credentials.get("GIPHY_API_KEY"):
        from nora.infra.giphy import GiphyClient

        giphy_client = GiphyClient(api_key=giphy_key)
    reaction_picker = None
    if cfg.reaction.enabled and giphy_client is not None:
        from nora.agent.reaction import ReactionPicker

        reaction_picker = ReactionPicker(
            client=_DebriefModelClient(
                base_url=cfg.endpoint("reaction")[0],
                api_key=cfg.endpoint("reaction")[1],
                max_tokens=cfg.reaction.max_tokens,
                temperature=0.3,
                model=cfg.endpoint("reaction", cfg.reaction.model or cfg.llm.model)[2],
                timeout_seconds=cfg.reaction.timeout_seconds,
                reasoning_effort=_reasoning_effort(
                    cfg.reaction.reasoning_effort or cfg.llm.reasoning_effort,
                    source="[reaction] reasoning_effort",
                ),
                agent_name="reaction",
            ),
            prompts=doctrine_store,
            gifs=giphy_client,
            metrics=metrics,
            timeout_seconds=cfg.reaction.timeout_seconds,
            content_checks=content_checks,
        )
    return ReactionsWiring(giphy_client=giphy_client, reaction_picker=reaction_picker)


@dataclass(frozen=True)
class RelayWiring:
    relay: Any


async def relay(
    allowlist, content_checks, metrics, presence_reader, slots, fire_path
) -> RelayWiring:
    # DEC-105: the cross-room relay. It does NOT send — it occasions a fire turn into the
    # destination (DEC-99's ruling), so R-1b's three links hold and the relayed message is
    # audited into the room it lands in. `fire_path` is genuinely circular: `FirePath`…
    async def _relay_speak(occasion: Any) -> str:
        fp = fire_path()
        if fp is None:
            # Every occasion needs a fire path, and this deployment has none
            # (heartbeat, participation and compute all off). `failed_error` is
            # what `send_to` turns into an honest «I couldn't».
            log.warning("relay asked to speak with no fire path wired")
            return "failed_error"
        return await fp.speak(occasion)

    relay = Relay(
        policy=allowlist,
        speak=_relay_speak,
        slots=slots,
        occasion_for=relay_occasion,
        metrics=metrics,
        # The SAME reader `<presence>` renders from,
        # so «Discord is down» in the block and `send_to` refusing into Discord
        # are one read of one mapping, never two opinions.
        transport=presence_reader.state,
        content_checks=content_checks,
    )
    return RelayWiring(relay=relay)
