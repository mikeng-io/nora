"""Finite request-aware responses for the production cognition and fire journey.

Dispatch is by the request's own production characteristics — the model lane,
the `<dreamer_objective>` tag the Dreamer stamps into its passes, the offered
tool schemas, the unit fold's scope marker — never one global phase or the
assumption that every unstreamed request is a judge. A request that matches no
applicable occasion is a recorded, named failure, never a silent answer.
"""

import html
import json
import re
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from aiohttp import web
from jsonschema import ValidationError, validate

INITIAL = "記低：the generated venue and time remain undecided."
CORRECTION = "記低：the generated venue is Cedar Hall; the generated time remains undecided."
TITLE = "Generated venue and time"
QUESTION = "Which generated venue and time should be selected?"
FOLLOWUP = "Which generated venue was finally selected?"
ANSWER = "The selected generated venue is Cedar Hall."

#: The system-role marker `fold_prompt(unit=True)` appends — the ONLY wire
#: difference between the unit fold (`{"items": [...]}` contract) and the
#: room fold (`{"standing": ...}` contract).
_UNIT_SCOPE_MARKER = "Scope of this call: UNIT"

#: The tag `DreamerRuntime` wraps each adaptor pass in
#: (`nora.instructions.dreamer.OBJECTIVE_TAG`). The Cognition session is
#: instructed by its objective BODY alone — no wrapper tag — so a tagged
#: request is always a background adaptor pass, never scenario work.
_OBJECTIVE_TAG = re.compile(r'<dreamer_objective name="([^"]+)"')

#: Dreamer objectives sharing `EntitySessions`' verbs whose finite answer is
#: the designed "nothing to change" close — plain text, no tool call. A
#: `character_no_change(entity=...)` needs the entity's name exactly as the
#: session's inputs named it, which a generated world cannot invent; an
#: unsettled close holds the adaptor's watermark, honestly.
_SESSION_CLOSE_OBJECTIVES = frozenset({"character-person", "character-unit", "character-nora"})

#: Total background calls the finite world answers. A workload bound, not a
#: threshold production raised: past it the request is a named `exhausted`
#: error like every other unsupported occasion.
_BACKGROUND_LIMIT = 10


class UnmatchedError(Exception):
    """A named finite-world refusal: `(kind, detail)` into `errors`."""

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


def completion(model, content, *, stream=False):
    response = {
        "id": "generated-response",
        "object": "chat.completion",
        "created": 1,
        "model": model,
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
    }
    result = {"status": 200, "headers": {"x-omniroute-model": model + "-served"}}
    if stream:
        chunks = [
            {
                **response,
                "object": "chat.completion.chunk",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": content},
                        "finish_reason": None,
                    }
                ],
            },
            {
                **response,
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            },
        ]
        result["headers"]["content-type"] = "text/event-stream"
        result["chunks"] = chunks
    else:
        result["json"] = response
    return {"method": "POST", "path": "/v1/chat/completions", "response": result}


def objects(value):
    if isinstance(value, str):
        with suppress(ValueError, TypeError):
            yield from objects(json.loads(value))
    elif isinstance(value, dict):
        yield value
        for child in value.values():
            yield from objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from objects(child)


def texts(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from texts(child)
    elif isinstance(value, list):
        for child in value:
            yield from texts(child)


class CausalProtocol:
    def __init__(self, cfg):
        self.cfg = cfg
        self.requests, self.errors = [], []
        self.phase = 0
        self.note_id = None
        self.first_id = None
        self.background = 0
        #: The served-request census by occasion: `scenario`, `fold_unit`,
        #: `fold_room`, `culture`, `character-*`. Served, not inferred —
        #: unmatched requests are in `errors`, never here.
        self.occasions: dict[str, int] = {}

    def tool(self, request, name, arguments):
        tools = {tool["function"]["name"]: tool["function"] for tool in request.get("tools", [])}
        if name not in tools:
            raise UnmatchedError("tool_not_offered", f"{name} not in {sorted(tools)}")
        try:
            validate(arguments, tools[name]["parameters"])
        except ValidationError as exc:
            raise UnmatchedError("schema_mismatch", f"{name}: {exc}") from exc
        result = completion(request["model"], "", stream=True)["response"]
        result["chunks"][0]["choices"][0]["delta"] = {
            "role": "assistant",
            "tool_calls": [
                {
                    "index": 0,
                    "id": f"generated-journey-{self.phase}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            ],
        }
        result["chunks"][1]["choices"][0]["finish_reason"] = "tool_calls"
        return result

    def source(self, text, words):
        matches = re.findall(r'<m n="(\d+)"[^>]*>(.*?)</m>', html.unescape(text), re.DOTALL)
        return next(int(mid) for mid, body in matches if words in html.unescape(body))

    def _fold(self, request, text):
        """One tools-less `nora` call: the standing fold. The system message
        names its scope — `Scope of this call: UNIT` asks for `items`, the
        room fold asks for `standing` and a missing key is `malformed` in
        production (`debrief._body`), so serving `items` to a room fold would
        answer a different contract than the one offered."""
        if _UNIT_SCOPE_MARKER in text:
            return "fold_unit", completion(request["model"], '{"items":[]}', stream=True)[
                "response"
            ]
        return "fold_room", completion(
            request["model"],
            json.dumps(
                {
                    "standing": "Generated protocol acknowledgement.",
                    "working_set": [],
                }
            ),
            stream=True,
        )["response"]

    def result(self, request):
        """One scenario call -> `(occasion, response)`. The occasion names
        what the request's own shape served — a `cognition` or `turn` model
        call, a `fold_unit`/`fold_room` fold — and every served scenario call
        advances `phase`, while a shape its phase does not expect is a
        recorded error that advances nothing."""
        text = "\n".join(texts(request["messages"]))
        tools = {t["function"]["name"] for t in request.get("tools", [])}
        assert request.get("stream") is True
        if self.phase == 0:
            assert request["model"] == self.cfg.dreamer.model and "remind" in tools
            self.first_id = self.source(text, INITIAL)
            return "cognition", self.tool(
                request,
                "note_create",
                {
                    "title": TITLE,
                    "description": "The generated venue and time remain undecided.",
                    "uncertainty": "Which generated venue and time?",
                    "closing_condition": "A generated venue and time are selected.",
                    "body": "## Decided\n## Open\n- Which generated venue and "
                    f"time? [{self.first_id}]",
                    "anchor_entities": ["Miles"],
                },
            )
        if self.phase == 1:
            created = next(
                row
                for row in objects(request["messages"])
                if row.get("status") == "ok" and row.get("note_id")
            )
            self.note_id = created["note_id"]
            at = datetime.now(UTC) + timedelta(seconds=self.cfg.reminders.min_lead_seconds + 5)
            return "cognition", self.tool(
                request,
                "remind",
                {
                    "intent": "ask",
                    "text": QUESTION,
                    "subject": f"note:{self.note_id}",
                    "action": "generated-venue-question",
                    "subject_rows": [f"note:{self.note_id}"],
                    "at": at.isoformat(),
                },
            )
        if self.phase == 2:
            assert any(
                row.get("status") == "ok" and row.get("reminder_id")
                for row in objects(request["messages"])
            )
            return "cognition", completion(
                request["model"], "Generated question is retained.", stream=True
            )["response"]
        if self.phase in {3, 8, 10}:
            assert not tools and request["model"] == self.cfg.llm.model
            return self._fold(request, text)
        if self.phase == 4:
            assert QUESTION in text and "send_reply" in tools
            return "turn", self.tool(request, "send_reply", {"text": QUESTION})
        if self.phase == 5:
            assert "note_revise" in tools and self.note_id in text and CORRECTION in text
            return "cognition", self.tool(request, "note_read", {"title": TITLE})
        if self.phase == 6:
            head = next(
                row
                for row in objects(request["messages"])
                if row.get("status") == "ok"
                and row.get("revision_id")
                and row.get("body")
                and row.get("title") == TITLE
            )
            mid = self.source(text, CORRECTION)
            return "cognition", self.tool(
                request,
                "note_revise",
                {
                    "note_id": self.note_id,
                    "parent_revision_id": head["revision_id"],
                    "body": (
                        f"## Decided\n- The generated venue is Cedar Hall (Miles) [{mid}]\n"
                        f"## Open\n- Which generated time? [{self.first_id}]"
                    ),
                },
            )
        if self.phase == 7:
            assert any(
                row.get("status") == "ok" and row.get("revision_id")
                for row in objects(request["messages"])
            )
            return "cognition", completion(
                request["model"], "Generated correction is retained.", stream=True
            )["response"]
        if self.phase == 9:
            # The tagged follow-up's own turn: its model call must carry the
            # question, the settled correction it answers on, and the real
            # delivery tool — the reply it returns rides the durable send.
            assert FOLLOWUP in text and CORRECTION in text and "send_reply" in tools
            return "turn", self.tool(request, "send_reply", {"text": ANSWER})
        raise UnmatchedError("exhausted", f"no generated journey phase {self.phase}")

    def _background(self, request, objective):
        """One Dreamer adaptor pass, by the objective the request declares.

        `culture` keeps its proven two-arm answer; the other wired
        `EntitySessions` sessions get the designed no-tool close — an
        unsettled run that holds their watermark rather than an invented
        entity write. Any other objective is not wired in this world and
        names a request production should not have made."""
        if self.background >= _BACKGROUND_LIMIT:
            raise UnmatchedError("exhausted", f"background passes past {_BACKGROUND_LIMIT}")
        if objective == "culture":
            if any(row.get("role") == "tool" for row in request["messages"]):
                assert any(row.get("status") == "ok" for row in objects(request["messages"]))
                return completion(
                    request["model"], "Generated culture pass is settled.", stream=True
                )["response"]
            return self.tool(
                request,
                "character_no_change",
                {"entity": "room", "why": "Generated protocol retains the previous account."},
            )
        if objective in _SESSION_CLOSE_OBJECTIVES:
            return completion(
                request["model"], "Generated protocol retains the previous account.", stream=True
            )["response"]
        raise UnmatchedError("unsupported_objective", objective)

    def answer(self, body):
        """Dispatch one captured request body -> `(scenario, occasion, response)`.

        `scenario` marks a call that advances the journey's conscious stage:
        the untagged dream-model turns, tag-lane turns and their folds. Every
        other served call names its background occasion and moves only
        `background`. Raises `UnmatchedError` for every named refusal — unknown
        model lane, unstreamed request on a streamed lane, unsupported
        objective or occasion, exhausted plan or background — and
        `AssertionError`/`StopIteration` from the scenario's own shape
        checks, so the caller records the failure rather than hiding it
        behind a canned answer."""
        model = body.get("model")
        if model == self.cfg.dreamer.model:
            if body.get("stream") is not True:
                raise UnmatchedError("mismatched_shape", f"{model} request is not streamed")
            tagged = _OBJECTIVE_TAG.search(
                html.unescape("\n".join(texts(body.get("messages", []))))
            )
            if tagged is not None:
                return False, tagged.group(1), self._background(body, tagged.group(1))
            occasion, response = self.result(body)
            return True, occasion, response
        if model == self.cfg.llm.model:
            if body.get("stream") is not True:
                raise UnmatchedError("mismatched_shape", f"{model} request is not streamed")
            occasion, response = self.result(body)
            return True, occasion, response
        if model == self.cfg.relevance_gate.model:
            raise UnmatchedError("unsupported_occasion", "relevance_gate")
        raise UnmatchedError("unknown_model", repr(model))

    def dispatch(self, body: dict) -> tuple[int, dict]:
        """Capture and answer one request — the world-facing entry.

        Every body lands in `requests` before it is read. A served request
        counts into `occasions` by the name it actually served and moves
        `phase` only when it was scenario work — a background pass can never
        consume the journey's stage. A refused request lands in `errors`
        under its kind and answers `(status, {"error": ...})`; nothing is
        dropped or silently answered."""
        self.requests.append(body)
        try:
            scenario, occasion, response = self.answer(body)
        except UnmatchedError as exc:
            self.errors.append((exc.kind, exc.detail))
            return 422, {"error": str(exc)}
        except (AssertionError, KeyError, StopIteration, ValueError) as exc:
            self.errors.append(("scenario_mismatch", f"{type(exc).__name__}: {exc}"))
            return 422, {"error": "unmatched generated journey request"}
        self.occasions[occasion] = self.occasions.get(occasion, 0) + 1
        if scenario:
            self.phase += 1
        else:
            self.background += 1
        return 200, response

    async def respond(self, request):
        try:
            body = await request.json()
        except ValueError as exc:
            self.errors.append(("malformed_request", type(exc).__name__))
            return web.json_response({"error": "malformed generated journey request"}, status=400)
        status, response = self.dispatch(body)
        if status != 200:
            return web.json_response(response, status=status)
        stream = web.StreamResponse(headers=response["headers"])
        await stream.prepare(request)
        for chunk in response["chunks"]:
            await stream.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await stream.write(b"data: [DONE]\n\n")
        return stream
