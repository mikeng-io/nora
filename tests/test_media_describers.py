"""Per-modality media describers (DEC-114).

The DISPATCH half: which modality a blob belongs to, whether that modality has a
describer, and what the tool says when it does not. The payload shape and the
prompt table are exercised against the real client in `tests/test_vision.py`;
config resolution lives in `tests/test_config.py`.

Every test here that means «the describer gap answered» uses a blob that IS in
the store. A blob the fixture never stores (`sha256="a"*64`) would make
`unavailable` arrive from the ABSENT-BLOB branch, and the test would stay green
when the branch it names is deleted outright. A status alone cannot tell those
two apart; the reason label can, and is asserted.
"""

# ruff: noqa: F811 — importing a pytest fixture and then naming it as a test
# parameter is the standard idiom for sharing one; ruff reads the parameter as a
# redefinition of the import. The alternative is duplicating `wiring`'s whole
# Postgres/blob-store setup here, which would be a second thing to keep in step.
from nora.config import Config, MediaConfig
from nora.infra.vision import DESCRIBE_PROMPTS, VisionResult
from tests.test_media_fetch import (  # noqa: F401 — fixtures are used by name
    PNG_SHA,
    _append_image,
    _call,
    wiring,
)


class _RecordingVision:
    """Records what the tool asked for, so «routed to the audio model» is
    asserted against the CALL and not against configuration."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def describe(
        self, *, data, media_type, prompt="", model="", provider="omniroute",
        max_bytes: int | None = None, windows: int | None = None,
    ):
        self.calls.append({
            "media_type": media_type, "prompt": prompt, "model": model,
            "provider": provider, "max_bytes": max_bytes,
        })
        return VisionResult(available=True, description="something")


# ---- the describer gap is `unavailable`, and for its OWN reason --------------

async def test_a_voice_note_is_unavailable_when_audio_is_switched_off(wiring):
    """R-27: a wiring gap is «I couldn't check», NEVER «there's nothing there».

    The blob IS stored (`PNG_SHA`) and the reason label is asserted, so deleting
    the describer-gap branch cannot pass by falling through to the absent-blob
    `unavailable`.
    """
    await _append_image(wiring["store"], conversation_id="g1",
                        sha256=PNG_SHA, media_type="audio/aac")
    tool = wiring["build"](describe_models={"image": "m/image"})
    out = await _call(tool, code=PNG_SHA[:6])
    assert out["status"] == "unavailable"
    assert wiring["vision"].calls == 0
    assert wiring["metrics"].get(
        "nora_media_fetch_total", status="unavailable", cached="false") == 1
    # The note has to name the modality, or she cannot say the true thing.
    assert "audio" in out["note"]


async def test_a_video_is_unavailable_when_video_is_switched_off(wiring):
    """Video is a modality of its own: a `_MODALITIES` without it would silently
    answer `not_an_image` — "that's a file" — for every video, the exact wrong
    answer the describer dispatch exists to stop giving."""
    await _append_image(wiring["store"], conversation_id="g1",
                        sha256=PNG_SHA, media_type="video/mp4")
    tool = wiring["build"](describe_models={"image": "m/image"})
    out = await _call(tool, code=PNG_SHA[:6])
    assert out["status"] == "unavailable"
    assert "video" in out["note"]


async def test_a_file_is_still_turned_away_before_any_model_call(wiring):
    """A modality nothing describes stays `not_an_image`, decided before the
    bytes reach any model."""
    await _append_image(wiring["store"], conversation_id="g1",
                        sha256=PNG_SHA, media_type="application/pdf")
    tool = wiring["build"](describe_models={"image": "m/image", "audio": "m/audio"})
    out = await _call(tool, code=PNG_SHA[:6])
    assert out["status"] == "not_an_image"
    assert wiring["vision"].calls == 0


# ---- naming a model lights the path up, with no code change -----------------

async def test_each_modality_is_described_by_its_own_model(wiring):
    """The seam: the AUDIO model must be the one asked for. Reverting
    `model=describe_model` on the describe call turns this red.

    The PROMPT half is asserted in `tests/test_vision.py` against the real
    client, not here — `media_fetch` never passes a prompt (the client resolves
    it from the MIME type), so a stub that replaces the client records `""` and
    any prompt assertion at this layer is vacuous by construction.
    """
    recorder = _RecordingVision()
    await _append_image(wiring["store"], conversation_id="g1",
                        sha256=PNG_SHA, media_type="audio/aac")
    tool = wiring["build"](vision_source=recorder,
                           describe_models={"image": "m/image", "audio": "m/audio"})
    out = await _call(tool, code=PNG_SHA[:6])
    assert out["status"] == "ok"
    assert recorder.calls[-1]["model"] == "m/audio"


async def test_an_image_still_reaches_the_image_model(wiring):
    """The shipped path must not move when the audio one is added."""
    recorder = _RecordingVision()
    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA)
    tool = wiring["build"](vision_source=recorder,
                           describe_models={"image": "m/image", "audio": "m/audio"})
    out = await _call(tool, code=PNG_SHA[:6])
    assert out["status"] == "ok"
    assert recorder.calls[-1]["model"] == "m/image"


async def test_modality_provider_is_passed_to_the_describer(wiring):
    """The provider the modality is configured with must be the one asked FOR,
    on the CALL — `media_fetch` cannot assume omniroute once the seam exists.
    Reverting `provider=describe_provider` turns this red."""
    recorder = _RecordingVision()
    await _append_image(wiring["store"], conversation_id="g1",
                        sha256=PNG_SHA, media_type="video/mp4")
    tool = wiring["build"](
        vision_source=recorder,
        describe_models={"video": "k3-256k"},
        describe_providers={"video": "kimi"},
    )
    out = await _call(tool, code=PNG_SHA[:6])
    assert out["status"] == "ok"
    assert recorder.calls[-1]["provider"] == "kimi"


async def test_modality_defaults_to_omniroute_provider(wiring):
    """A modality with no provider map entry (every pre-provider caller) still
    travels through the gateway — the default must be explicit on the call so
    the tool and the client cannot disagree."""
    recorder = _RecordingVision()
    await _append_image(wiring["store"], conversation_id="g1", sha256=PNG_SHA)
    tool = wiring["build"](
        vision_source=recorder,
        describe_models={"image": "m/image"},
        # no describe_providers at all — pre-provider shape
    )
    out = await _call(tool, code=PNG_SHA[:6])
    assert out["status"] == "ok"
    assert recorder.calls[-1]["provider"] == "omniroute"


# ---- the composition root: config -> the mapping the tool reads --------------

def _cfg_with(**describe) -> Config:
    return Config.model_construct(media=MediaConfig(describe=describe))


def test_the_composition_root_offers_only_enabled_describers_that_name_a_model():
    """`_describe_models` is the single joint between config and the tool, and
    where the `vision_model` alias resolution lands. An empty loop would leave
    every photo answering «I can't take in image».
    """
    from nora.app.composition import _describe_models

    resolved = _describe_models(_cfg_with(
        image={"enabled": True, "model": "m/image"},
        audio={"enabled": False, "model": "m/audio"},   # off: excluded
        video={"enabled": True, "model": ""},           # no model: excluded
    ))
    assert resolved == {"image": "m/image"}


def test_the_composition_root_resolves_the_legacy_alias():
    """A pre-split config names only `vision_model`; images must still be
    described, and the alias must be folded in exactly once — here."""
    from nora.app.composition import _describe_models

    cfg = Config.model_construct(media=MediaConfig(vision_model="legacy/only"))
    assert _describe_models(cfg) == {"image": "legacy/only"}


def test_the_composition_root_offers_providers_only_for_live_describers():
    """`_describe_providers` must track `_describe_models` — a modality that
    cannot describe appears in NEITHER map, and one that can describe defaults
    to omniroute unless the config names kimi."""
    from nora.app.composition import _describe_providers

    resolved = _describe_providers(_cfg_with(
        image={"enabled": True, "model": "m/image"},
        video={"enabled": True, "model": "k3-256k", "provider": "kimi"},
        audio={"enabled": False, "model": "m/audio"},
    ))
    assert resolved == {"image": "omniroute", "video": "kimi"}


def test_the_kimi_key_env_name_resolves_through_api_key_env():
    """`_direct_lane_key_env` must honour `api_key_env` when a kimi describer
    names one, and fall back to `KIMI_CODE_API_KEY` when it does not — the
    contract `DescriberConfig.api_key_env`'s docstring promises (DEC-8: config
    names the env var, never the key)."""
    from nora.app.composition import _direct_lane_key_env

    declared = _direct_lane_key_env(_cfg_with(
        image={"enabled": True, "model": "m/image"},
        video={"enabled": True, "model": "k3-256k",
               "provider": "kimi", "api_key_env": "MY_KIMI_KEY"},
    ), "kimi")
    assert declared == "MY_KIMI_KEY"

    defaulted = _direct_lane_key_env(_cfg_with(
        image={"enabled": True, "model": "m/image"},
        video={"enabled": True, "model": "k3-256k", "provider": "kimi"},
    ), "kimi")
    assert defaulted == "KIMI_CODE_API_KEY"

    nothing_kimi = _direct_lane_key_env(_cfg_with(
        image={"enabled": True, "model": "m/image"},
    ), "kimi")
    assert nothing_kimi == "KIMI_CODE_API_KEY"


def test_the_opencode_go_key_env_name_is_its_own_not_kimis():
    """Each direct lane reads ITS key: an `opencode-go` describer falls back to
    `OPENCODE_GO_API_KEY`, and a kimi describer's `api_key_env` beside it does
    not leak across — one key source per lane (DEC-197)."""
    from nora.app.composition import _direct_lane_key_env

    cfg = _cfg_with(
        audio={"enabled": True, "model": "mimo-v2.6-flash", "provider": "opencode-go"},
        video={"enabled": True, "model": "k3-256k",
               "provider": "kimi", "api_key_env": "MY_KIMI_KEY"},
    )
    assert _direct_lane_key_env(cfg, "opencode-go") == "OPENCODE_GO_API_KEY"
    assert _direct_lane_key_env(cfg, "kimi") == "MY_KIMI_KEY"


def test_a_direct_lane_whose_key_is_unset_is_not_built(monkeypatch):
    """Fail-closed at the composition root: a selected lane with no key is
    LEFT OUT of the direct clients, so `VisionClient` refuses that provider
    (`unavailable`) rather than building a keyless client or falling back to
    OmniRoute. A lane nothing selects is not built at all."""
    from nora.app.composition import _direct_describer_clients

    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)
    monkeypatch.setenv("KIMI_CODE_API_KEY", "kimi-key")
    cfg = _cfg_with(
        audio={"enabled": True, "model": "mimo-v2.6-flash", "provider": "opencode-go"},
        video={"enabled": True, "model": "k3-256k", "provider": "kimi"},
    )
    assert set(_direct_describer_clients(cfg)) == {"kimi"}

    monkeypatch.setenv("OPENCODE_GO_API_KEY", "go-key")
    assert set(_direct_describer_clients(cfg)) == {"kimi", "opencode-go"}

    only_omniroute = _cfg_with(image={"enabled": True, "model": "m/image"})
    assert _direct_describer_clients(only_omniroute) == {}


# ---- the registry joint ------------------------------------------------------

def test_the_registry_hands_the_describers_to_the_tool():
    """`ToolRegistry` must thread `describe_models` through: setting it to `{}`
    on the way discards every describer. Config -> composition -> registry ->
    tool is three joints; this pins the one in the middle."""
    from nora.agent.registry import ToolRegistry

    registry = ToolRegistry(describe_models={"image": "m/image", "audio": "m/audio"})
    assert registry._describe_models == {"image": "m/image", "audio": "m/audio"}


def test_the_registry_hands_the_providers_to_the_tool():
    """The provider map threads alongside the model map — dropping it would
    silently route every modality back to omniroute (which drops video_url)."""
    from nora.agent.registry import ToolRegistry

    registry = ToolRegistry(
        describe_models={"video": "kimi-code/k3-256k"},
        describe_providers={"video": "kimi"},
    )
    assert registry._describe_providers == {"video": "kimi"}


def test_every_describable_modality_has_an_authored_prompt():
    """The three tables that have to agree — the config fields, the tool's
    dispatch list, and the prompt table — are written in three files. A modality
    in one and not another is a silent `_PROMPT` fallback ("Describe this image"
    asked about a voice note)."""
    from nora.agent.tools import _MODALITIES
    from nora.config import DescribersConfig

    assert set(_MODALITIES) == set(DESCRIBE_PROMPTS)
    assert set(_MODALITIES) == set(DescribersConfig.model_fields)
