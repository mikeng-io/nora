"""Image generation provider factory — the only place that maps a
`[image_gen].provider` name to a concrete `ImageGenerator` implementation.

DEC-137: the port is provider-agnostic; the composition root asks for a
named provider and this module answers: `xai` (DEC-137) or `openai_codex`
(DEC-304, design/proposed/29 §11). Any other `provider` value degrades to
None (M6): the `generate_image` tool answers `unavailable` with
a composition-side warning, exactly like an unset token — never a raise at
boot and never a guessed provider.
"""

from __future__ import annotations

from pathlib import Path

from nora.domain.ports import ImageGenerator

from .openai_codex_adapter import OpenAICodexImageGenerator
from .openai_codex_oauth import OpenAICodexOAuth
from .xai_adapter import XaiImageGenerator
from .xai_oauth import XaiOAuth


def build_image_generator(
    *,
    provider: str,
    refresh_token: str = "",
    state_file: Path | None = None,
    oauth: XaiOAuth | OpenAICodexOAuth | None = None,
    model: str = "grok-imagine-image",
    quality_model: str = "grok-imagine-image-quality",
    timeout_seconds: float = 90.0,
    responses_model: str = "gpt-5.5",
    standard_quality: str = "medium",
    high_quality: str = "high",
    max_reference_images: int | None = None,
) -> ImageGenerator | None:
    """Build the `ImageGenerator` the config selects.

    Pass an existing `oauth` (the process-lifetime lane the keepalive also
    holds, DEC-308: one refresh lock) OR a fresh `refresh_token` +
    `state_file` (for the CLI / one-shot paths).
    Returns None when the lane cannot be constructed (empty token, or an
    unknown provider — the honest wiring-gap state the tool answers
    `unavailable` for, never a crash and never a guessed provider). M6: an
    unknown provider name degrades to None with a composition-side warning,
    matching the weather/presence lanes, instead of raising at boot.
    """
    if provider == "openai_codex":
        # `refresh_token` is the BOOTSTRAP token from `.env`; the live,
        # rotating one is kept in `state_file` (openai_codex_oauth).
        codex = oauth if isinstance(oauth, OpenAICodexOAuth) else None
        if codex is None:
            if not refresh_token or state_file is None:
                return None
            codex = OpenAICodexOAuth(
                bootstrap_refresh_token=refresh_token, state_file=state_file,
            )
        return OpenAICodexImageGenerator(
            oauth=codex,
            model=model,
            responses_model=responses_model,
            standard_quality=standard_quality,
            high_quality=high_quality,
            timeout_seconds=timeout_seconds,
            max_reference_images=max_reference_images,
        )
    if provider == "xai":
        lane = oauth if isinstance(oauth, XaiOAuth) else None
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
            max_reference_images=max_reference_images,
        )
    # M6 (Kimi audit): an unknown provider must DEGRADE, not crash the
    # production daemon at boot — the same fail-soft posture every sibling
    # lane (weather, presence) takes. A typo'd `provider = "foo"` now leaves
    # the `generate_image` tool answering `unavailable` with a logged
    # warning, instead of ValueError taking down the whole agent. The
    # composition root logs the warning (it owns the config context).
    return None


__all__ = [
    "OpenAICodexImageGenerator",
    "OpenAICodexOAuth",
    "XaiImageGenerator",
    "XaiOAuth",
    "build_image_generator",
]
