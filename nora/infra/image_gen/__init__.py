"""Image generation provider factory — the only place that maps a
`[image_gen].provider` name to a concrete `ImageGenerator` implementation.

DEC-137: the port is provider-agnostic; the composition root asks for a
named provider and this module answers. `provider` values other than `xai`
degrade to None (M6): the `generate_image` tool answers `unavailable` with
a composition-side warning, exactly like an unset token — never a raise at
boot and never a guessed provider.
"""

from __future__ import annotations

from pathlib import Path

from nora.domain.ports import ImageGenerator

from .xai_adapter import XaiImageGenerator
from .xai_oauth import XaiOAuth


def build_image_generator(
    *,
    provider: str,
    refresh_token: str = "",
    state_file: Path | None = None,
    oauth: XaiOAuth | None = None,
    model: str = "grok-imagine-image",
    quality_model: str = "grok-imagine-image-quality",
    timeout_seconds: float = 90.0,
) -> ImageGenerator | None:
    """Build the `ImageGenerator` the config selects.

    Pass an existing `oauth` (process-lifetime lane with refresh lock) OR a
    fresh `refresh_token` + `state_file` (for the CLI / one-shot paths).
    Returns None when the lane cannot be constructed (empty token, or an
    unknown provider — the honest wiring-gap state the tool answers
    `unavailable` for, never a crash and never a guessed provider). M6: an
    unknown provider name degrades to None with a composition-side warning,
    matching the weather/presence lanes, instead of raising at boot.
    """
    if provider == "xai":
        lane = oauth
        if lane is None:
            if not refresh_token or state_file is None:
                return None
            lane = XaiOAuth(
                refresh_token=refresh_token,
                state_file=state_file,
                timeout_seconds=timeout_seconds,
            )
        return XaiImageGenerator(
            oauth=lane,
            model=model,
            quality_model=quality_model,
            timeout_seconds=timeout_seconds,
        )
    # M6 (Kimi audit): an unknown provider must DEGRADE, not crash the
    # production daemon at boot — the same fail-soft posture every sibling
    # lane (weather, presence) takes. A typo'd `provider = "foo"` now leaves
    # the `generate_image` tool answering `unavailable` with a logged
    # warning, instead of ValueError taking down the whole agent. The
    # composition root logs the warning (it owns the config context).
    return None


__all__ = ["XaiImageGenerator", "XaiOAuth", "build_image_generator"]
