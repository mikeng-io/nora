"""The `voice` contract (the audio controller's control plane) and `intake`'s `record`.

Its routes, closed kinds, effects and payloads build on `nora.domain.service_api`
and the `platform` route table; the OpenAPI document in
`docs/service-api/openapi.json` is generated from them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal

from nora.domain.service_api import OperationEnvelope, OperationRequest, ReadEnvelope
from nora.domain.service_api_platform import OperationError, Route

VoiceOperationKind = Literal[
    "join", "leave", "play", "stop", "pause", "resume", "set_volume", "speak", "report",
]
VoiceQueryKind = Literal["status"]
IntakeOperationKind = Literal["record"]


@dataclass(frozen=True)
class JoinEffect:
    """Connect the guild's voice connection to a channel."""

    guild_id: str
    channel_id: str


@dataclass(frozen=True)
class ConnectionEffect:
    """`leave`, `stop`, `pause` and `resume`: the generation the caller observed."""

    guild_id: str
    generation: str


@dataclass(frozen=True)
class PlayEffect:
    """Play a stream on the connection."""

    guild_id: str
    generation: str
    stream_url: str
    title: str | None = None
    duration_seconds: float | None = None
    resumable: bool | None = None


@dataclass(frozen=True)
class SetVolumeEffect:
    """Set the player's volume, in percent of unity."""

    guild_id: str
    generation: str
    percent: float


@dataclass(frozen=True)
class SpeakEffect:
    """Speak bounded audio on the connection; its record keeps the hash, never the bytes."""

    guild_id: str
    generation: str
    audio_b64: str
    media_type: str


@dataclass(frozen=True)
class ReportEffect:
    """A controller's connection event, sent to the hub; the event's own fields ride along."""

    event_id: str
    event_seq: int


@dataclass(frozen=True)
class UtteranceEffect:
    """A woken utterance; the wake gate mints `event_id`."""

    event_id: str
    kind: Literal["utterance"]
    guild_id: str
    user_id: str
    text: str
    voice_channel_id: str | None = None


VoiceEffect = (
    JoinEffect | ConnectionEffect | PlayEffect | SetVolumeEffect | SpeakEffect | ReportEffect
)
VOICE_EFFECTS: Final[Mapping[VoiceOperationKind, type]] = {
    "join": JoinEffect,
    "leave": ConnectionEffect,
    "play": PlayEffect,
    "stop": ConnectionEffect,
    "pause": ConnectionEffect,
    "resume": ConnectionEffect,
    "set_volume": SetVolumeEffect,
    "speak": SpeakEffect,
    "report": ReportEffect,
}
INTAKE_EFFECTS: Final[Mapping[IntakeOperationKind, type]] = {"record": UtteranceEffect}
#: Effects whose object admits fields beyond the ones declared.
OPEN_EFFECTS: Final[frozenset[type]] = frozenset({ReportEffect})


@dataclass(frozen=True)
class VoiceOperationRequest(OperationRequest):
    """A connection operation or a report; `effect` is the shape `VOICE_EFFECTS` names."""

    kind: VoiceOperationKind
    effect: VoiceEffect


@dataclass(frozen=True)
class VoiceOperationPayload:
    """The controller's connection snapshot, and on a refusal its reason."""

    state: Mapping[str, object] | None = None
    error: OperationError | None = None


@dataclass(frozen=True)
class VoiceOperationEnvelope(OperationEnvelope):
    """The answer to a voice operation; a `report`'s payload is the hub's own answer."""

    payload: VoiceOperationPayload | Mapping[str, object]


@dataclass(frozen=True)
class VoiceOperationReadEnvelope(ReadEnvelope):
    """A voice operation read back: `ok` carries the operation's own envelope."""

    payload: VoiceOperationEnvelope | None


@dataclass(frozen=True)
class VoiceStatusParams:
    """The guild whose connection is read."""

    guild_id: str


@dataclass(frozen=True)
class VoiceQuery:
    """A read of a connection's status."""

    kind: VoiceQueryKind
    params: VoiceStatusParams


@dataclass(frozen=True)
class VoiceStatusReadEnvelope(ReadEnvelope):
    """A connection's status, in the implementer's own shape; none is `nothing_found`."""

    payload: Mapping[str, object] | None


@dataclass(frozen=True)
class IntakeOperationRequest(OperationRequest):
    """An intake event recorded by operation."""

    kind: IntakeOperationKind
    effect: UtteranceEffect


@dataclass(frozen=True)
class IntakeOperationPayload:
    """Why an event was not recorded, when it was not."""

    error: OperationError | None = None


@dataclass(frozen=True)
class IntakeOperationEnvelope(OperationEnvelope):
    """The answer to an intake `record`."""

    payload: IntakeOperationPayload


@dataclass(frozen=True)
class IntakeOperationReadEnvelope(ReadEnvelope):
    """An intake operation read back: `ok` carries the operation's own envelope."""

    payload: IntakeOperationEnvelope | None


#: Each operation request's kind, mapped to the effect it carries.
EFFECTS: Final[Mapping[
    type, Mapping[VoiceOperationKind, type] | Mapping[IntakeOperationKind, type],
]] = {
    VoiceOperationRequest: VOICE_EFFECTS,
    IntakeOperationRequest: INTAKE_EFFECTS,
}

VOICE_ROUTES: Final[tuple[Route, ...]] = (
    Route("submit_voice_operation", "POST", "/v1/voice/operations", "operation",
          VoiceOperationEnvelope, VoiceOperationRequest, conflicts=True),
    Route("read_voice_operation", "GET", "/v1/voice/operations/{operation_id}", "read",
          VoiceOperationReadEnvelope),
    Route("voice_query", "POST", "/v1/voice/queries", "read",
          VoiceStatusReadEnvelope, VoiceQuery),
    Route("submit_intake_operation", "POST", "/v1/intake/operations", "operation",
          IntakeOperationEnvelope, IntakeOperationRequest, conflicts=True),
    Route("read_intake_operation", "GET", "/v1/intake/operations/{operation_id}", "read",
          IntakeOperationReadEnvelope),
)
