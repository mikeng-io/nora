"""Finite request-aware responses for the production cognition and fire journey."""

import html
import json
import re
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from aiohttp import web
from jsonschema import validate

INITIAL = "記低：the generated venue and time remain undecided."
CORRECTION = "記低：the generated venue is Cedar Hall; the generated time remains undecided."
TITLE = "Generated venue and time"
QUESTION = "Which generated venue and time should be selected?"
FOLLOWUP = "Which generated venue was finally selected?"
ANSWER = "The selected generated venue is Cedar Hall."


def completion(model, content, *, stream=False):
    response = {"id": "generated-response", "object": "chat.completion", "created": 1,
                "model": model, "choices": [{"index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": content}}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}}
    result = {"status": 200, "headers": {"x-omniroute-model": model + "-served"}}
    if stream:
        chunks = [{**response, "object": "chat.completion.chunk", "choices": [{
            "index": 0, "delta": {"role": "assistant", "content": content},
            "finish_reason": None}]}, {**response, "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}]
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
        self.app = web.Application()
        self.app.router.add_post("/v1/chat/completions", self.respond)

    def tool(self, request, name, arguments):
        tools = {tool["function"]["name"]: tool["function"] for tool in request.get("tools", [])}
        assert name in tools, (self.phase, name, tuple(tools))
        validate(arguments, tools[name]["parameters"])
        result = completion(request["model"], "", stream=True)["response"]
        result["chunks"][0]["choices"][0]["delta"] = {"role": "assistant", "tool_calls": [{
            "index": 0, "id": f"generated-journey-{self.phase}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)}}]}
        result["chunks"][1]["choices"][0]["finish_reason"] = "tool_calls"
        return result

    def source(self, text, words):
        matches = re.findall(r'<m n="(\d+)"[^>]*>(.*?)</m>', html.unescape(text), re.DOTALL)
        return next(int(mid) for mid, body in matches if words in html.unescape(body))

    def result(self, request):
        text = "\n".join(texts(request["messages"]))
        tools = {t["function"]["name"] for t in request.get("tools", [])}
        assert request.get("stream") is True
        if self.phase == 0:
            assert request["model"] == self.cfg.dreamer.model and "remind" in tools
            self.first_id = self.source(text, INITIAL)
            return self.tool(request, "note_create", {"title": TITLE,
                "description": "The generated venue and time remain undecided.",
                "uncertainty": "Which generated venue and time?",
                "closing_condition": "A generated venue and time are selected.",
                "body": f"## Decided\n## Open\n- Which generated venue and time? [{self.first_id}]",
                "anchor_entities": ["Miles"]})
        if self.phase == 1:
            created = next(row for row in objects(request["messages"])
                           if row.get("status") == "ok" and row.get("note_id"))
            self.note_id = created["note_id"]
            at = datetime.now(UTC) + timedelta(seconds=self.cfg.reminders.min_lead_seconds + 5)
            return self.tool(request, "remind", {"intent": "ask", "text": QUESTION,
                "subject": f"note:{self.note_id}", "action": "generated-venue-question",
                "subject_rows": [f"note:{self.note_id}"], "at": at.isoformat()})
        if self.phase == 2:
            assert any(row.get("status") == "ok" and row.get("reminder_id")
                       for row in objects(request["messages"]))
            return completion(request["model"], "Generated question is retained.",
                              stream=True)["response"]
        if self.phase in {3, 8, 10}:
            assert not tools and request["model"] == self.cfg.llm.model
            return completion(request["model"], '{"items":[]}', stream=True)["response"]
        if self.phase == 4:
            assert QUESTION in text and "send_reply" in tools
            return self.tool(request, "send_reply", {"text": QUESTION})
        if self.phase == 5:
            assert "note_revise" in tools and self.note_id in text and CORRECTION in text
            return self.tool(request, "note_read", {"title": TITLE})
        if self.phase == 6:
            head = next(row for row in objects(request["messages"])
                        if row.get("status") == "ok" and row.get("revision_id")
                        and row.get("body") and row.get("title") == TITLE)
            mid = self.source(text, CORRECTION)
            return self.tool(request, "note_revise", {"note_id": self.note_id,
                "parent_revision_id": head["revision_id"], "body": (
                    f"## Decided\n- The generated venue is Cedar Hall (Miles) [{mid}]\n"
                    f"## Open\n- Which generated time? [{self.first_id}]")})
        if self.phase == 7:
            assert any(row.get("status") == "ok" and row.get("revision_id")
                       for row in objects(request["messages"]))
            return completion(request["model"], "Generated correction is retained.",
                              stream=True)["response"]
        if self.phase == 9:
            # The tagged follow-up's own turn: its model call must carry the
            # question, the settled correction it answers on, and the real
            # delivery tool — the reply it returns rides the durable send.
            assert (FOLLOWUP in text and CORRECTION in text
                    and "send_reply" in tools)
            return self.tool(request, "send_reply", {"text": ANSWER})
        raise AssertionError(f"unmatched generated journey phase {self.phase}")

    async def respond(self, request):
        body = await request.json()
        self.requests.append(body)
        from nora.subject.eval_evidence import current

        if evidence := current():
            evidence.record("fixture_model_request", phase=self.phase, body=body)
        try:
            is_culture = any(t.get("function", {}).get("name") == "character_no_change"
                             for t in body.get("tools", []))
            if is_culture:
                assert '<dreamer_objective name="culture">' in "\n".join(texts(body["messages"]))
                assert body["model"] == self.cfg.dreamer.model and self.background < 10
                if any(row.get("role") == "tool" for row in body["messages"]):
                    assert any(row.get("status") == "ok" for row in objects(body["messages"]))
                    response = completion(body["model"], "Generated culture pass is settled.",
                                          stream=True)["response"]
                else:
                    response = self.tool(body, "character_no_change", {
                        "entity": "room",
                        "why": "Generated protocol retains the previous account."})
                self.background += 1
            else:
                response = self.result(body)
        except (AssertionError, KeyError, StopIteration, ValueError) as exc:
            self.errors.append((self.phase, type(exc).__name__, str(exc)))
            return web.json_response({"error": "unmatched generated journey request"}, status=422)
        if not is_culture:
            self.phase += 1
        stream = web.StreamResponse(headers=response["headers"])
        await stream.prepare(request)
        for chunk in response["chunks"]:
            await stream.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await stream.write(b"data: [DONE]\n\n")
        return stream
