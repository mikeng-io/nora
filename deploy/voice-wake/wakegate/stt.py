"""STT + Nora forwarding for completed utterances.

One POST against the local `stt` service's OpenAI-compatible
`/v1/audio/transcriptions` (private `nora` network — audio never leaves the
box), multipart built by hand exactly like `nora/infra/speech.py`'s
`SenseVoiceTranscriber`: the OpenAI contract wants the audio under a `file`
part with a filename. `model` names the engine in `deploy/stt/server.py`'s
`MODEL_CONFIGS` (`noise-detection`, the primary).

Failure posture is the wake gate's own: **no wake, no cost** — an STT
failure retries ONCE with `language` unset (auto-LID instead of the pinned
`yue`, in case the pin is what the engine choked on) and then drops the
utterance silently. Nothing is queued and nothing is retried later: audio
is transient by design.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import secrets
import uuid
from pathlib import Path

import aiohttp
from jsonschema import Draft202012Validator

log = logging.getLogger("wakegate.stt")


def _multipart(model: str, language: str | None, wav: bytes) -> tuple[str, bytes]:
    boundary = "----wakegate-stt"
    parts = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="model"\r\n\r\n'
        f"{model}\r\n"
    )
    if language is not None:
        parts += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="language"\r\n\r\n'
            f"{language}\r\n"
        )
    parts += (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="utterance.wav"\r\n'
        f"Content-Type: audio/wav\r\n\r\n"
    )
    return boundary, parts.encode() + wav + f"\r\n--{boundary}--\r\n".encode()


async def transcribe(
    session: aiohttp.ClientSession,
    *,
    stt_url: str,
    model: str,
    language: str | None,
    wav: bytes,
) -> str | None:
    """One utterance -> transcript text, or None (dropped). Never raises:
    a dead STT service must not take the wake loop down with it."""
    url = stt_url.rstrip("/") + "/v1/audio/transcriptions"
    for attempt_language in (language, None) if language is not None else (None,):
        boundary, body = _multipart(model, attempt_language, wav)
        try:
            async with session.post(
                url,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                data=body,
            ) as response:
                if response.status != 200:
                    log.warning("stt refused (http %s, language=%r)", response.status, attempt_language)
                    continue
                payload = await response.json(content_type=None)
                text = (payload or {}).get("text") or ""
                if not text:
                    log.info("stt returned an empty transcript (language=%r)", attempt_language)
                    continue
                return str(text)
        except asyncio.CancelledError:
            raise  # shutdown is not a transcription failure
        except (TimeoutError, aiohttp.ClientError) as exc:
            log.warning("stt unreachable (%s, language=%r)", type(exc).__name__, attempt_language)
        except Exception:
            log.exception("transcribe failed (language=%r)", attempt_language)
    return None


# The canonical service API (design/service-api/conventions.md). Shapes are
# the contract's generated schema; what a schema cannot say is checked here:
# the status an outcome is answered with, `settled` on all but `pending`, and
# the installation scope.
_STATUS = {"complete": 200, "pending": 202, "denied": 403, "rejected": 200,
           "unsupported": 200, "incomplete": 200, "uncertain": 200,
           "unavailable": 503}
_READ_STATUS = {"ok": 200, "nothing_found": 200, "unavailable": 503}
_NO_ENVELOPE = frozenset({400, 401, 404, 409, 422})
_SCHEMA = json.loads(Path(__file__).with_name("intake_schema.json").read_text())
_OPERATION = Draft202012Validator(
    {**_SCHEMA, "$ref": "#/components/schemas/IntakeOperationEnvelope"})
_READ = Draft202012Validator(
    {**_SCHEMA, "$ref": "#/components/schemas/IntakeOperationReadEnvelope"})
# A lost or unsettled answer is read back by its operation_id, never resent.
# The POST, every read-back and the gaps share one deadline, the session's
# total timeout: each request gets only what is left of it.
_READ_BACK_ON = frozenset({"pending", "uncertain", "lost"})
_READ_BACKS = 3
_READ_BACK_GAP_S = 0.2


def _operation_outcome(body: object) -> str | None:
    """The outcome of a valid intake operation envelope, else None."""
    if not _OPERATION.is_valid(body):
        return None
    outcome = body["outcome"]
    settled = body["when"]["state"] == "settled"
    if settled == (outcome == "pending") or body["scope"]["level"] != "installation":
        return None
    return outcome


async def forward_utterance(
    session: aiohttp.ClientSession,
    *,
    nora_url: str,
    user_id: str,
    text: str,
    guild_id: str,
    token: str,
    voice_channel_id: str | None = None,
) -> bool:
    """The transcript -> Nora's `POST /v1/intake/operations` as a `record`
    operation. Text only — audio never crosses this boundary. Never raises;
    a refused forward drops the utterance (transient by design) and is
    visible in the log.

    True only for an envelope valid against the contract whose outcome is
    `complete`, answered with that outcome's status. On `pending`,
    `uncertain` or a lost or invalid answer the record is read back by the
    same `operation_id`, up to three times; only a settled `complete` there
    is True. Every other answer, a redirect included, is False after one
    POST. The POST, the read-backs and the gaps share one deadline, the
    session's total timeout, of which the POST may use at most half.

    `token` is the shared service bearer; Nora refuses the utterance without it.

    The GUILD is what is sent, not a conversation id: nora resolves it to the
    room the operator marked `voice = true`, so this service never has to know
    — or be reconfigured when — a guild's rooms change.

    `voice_channel_id` is provenance and OPTIONAL. It is whatever the
    controller reports it is CURRENTLY connected to; omitted when that could
    not be read, because a stale channel id in a trace is worse than none."""
    url = nora_url.rstrip("/") + "/v1/intake/operations"
    effect: dict[str, str] = {
        "event_id": str(uuid.uuid4()),
        "kind": "utterance",
        "guild_id": guild_id,
        "user_id": user_id,
        "text": text,
    }
    if voice_channel_id:
        effect["voice_channel_id"] = voice_channel_id
    operation_id = str(uuid.uuid4())
    request = {"operation_id": operation_id, "kind": "record", "effect": effect}
    # One W3C trace per utterance, on the POST and every read-back.
    traceparent = f"00-{secrets.token_hex(16)}-{secrets.token_hex(8)}-01"
    headers = {"Authorization": f"Bearer {token}", "traceparent": traceparent}
    total = session.timeout.total
    now = asyncio.get_running_loop().time()
    deadline = None if total is None else now + total
    # The POST gets at most half the budget, so a read-back always fits after it.
    post_deadline = None if total is None else now + total / 2
    outcome = await _submit(session, url, request, headers, post_deadline)
    if outcome in _READ_BACK_ON:
        outcome = await _read_back(session, f"{url}/{operation_id}", headers, deadline)
    if outcome == "complete":
        return True
    if outcome in _READ_BACK_ON:
        log.warning("nora delivery unconfirmed (%s)", outcome)
    else:
        log.warning("nora did not record the utterance (%s)", outcome)
    return False


def _left(deadline: float | None) -> float | None:
    return None if deadline is None else deadline - asyncio.get_running_loop().time()


async def _answer(session: aiohttp.ClientSession, method: str, url: str,
                  deadline: float | None, **kwargs) -> tuple[int, object] | str:
    """(status, JSON body or None), or a fixed label for no answer."""
    left = _left(deadline)
    if left is not None and left <= 0:
        return "lost"
    # ceil_threshold=inf: aiohttp would otherwise round a long timer up to a second.
    timeout = (aiohttp.ClientTimeout(total=left, ceil_threshold=math.inf)
               if left is not None else session.timeout)
    try:
        async with session.request(method, url, allow_redirects=False,
                                   timeout=timeout, **kwargs) as response:
            try:
                body = await response.json(content_type=None)
            except ValueError:
                body = None
            return response.status, body
    except asyncio.CancelledError:
        raise
    except (TimeoutError, aiohttp.ClientError):
        return "lost"


async def _submit(session: aiohttp.ClientSession, url: str, request: dict,
                  headers: dict[str, str], deadline: float | None) -> str:
    """The POST's validated outcome, or a fixed label: `lost`, `redirect`, `http_<n>`."""
    answer = await _answer(session, "POST", url, deadline, json=request, headers=headers)
    if isinstance(answer, str):
        return answer
    status, body = answer
    if 300 <= status < 400:
        return "redirect"
    if status in _NO_ENVELOPE:
        return f"http_{status}"
    outcome = _operation_outcome(body)
    return outcome if outcome is not None and _STATUS[outcome] == status else "lost"


async def _read_back(session: aiohttp.ClientSession, url: str,
                     headers: dict[str, str], deadline: float | None) -> str:
    """The record's settled outcome by GET, or the last label once the bound
    or the deadline runs out."""
    label = "lost"
    for attempt in range(_READ_BACKS):
        if attempt:
            left = _left(deadline)
            if left is not None and left <= _READ_BACK_GAP_S:
                break
            await asyncio.sleep(_READ_BACK_GAP_S)
        answer = await _answer(session, "GET", url, deadline, headers=headers)
        if isinstance(answer, str):
            continue
        status, body = answer
        label = _read_outcome(status, body)
        if label == "nothing_found" or (label in _STATUS and label != "pending"):
            return label
    return label


def _read_outcome(status: int, body: object) -> str:
    """The read-back's answer: the record's outcome, `nothing_found`, or `lost`."""
    if (not _READ.is_valid(body) or _READ_STATUS[body["outcome"]] != status
            or body["scope"]["level"] != "installation"
            or body["outcome"] == "unavailable"):
        return "lost"
    if body["outcome"] == "nothing_found":
        return "nothing_found"
    return _operation_outcome(body["payload"]) or "lost"
