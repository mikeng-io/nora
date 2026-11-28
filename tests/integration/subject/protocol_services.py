"""Generated HTTP responses for the journey tests' model and bridge services."""

from __future__ import annotations

import json

from aiohttp import web

from nora.testing.causal_protocol import completion

MODELS = ["generated-a", "generated-b"]


def services(cfg, requests):
    rows = [completion(cfg.dreamer.model, "Generated terminal response.", stream=True),
            completion(cfg.llm.model, json.dumps({"items": []}), stream=True),
            *(completion(alias, json.dumps({"verdict": "met", "reason": "Generated wire control",
                                           "entry_ids": []})) for alias in MODELS)]
    model = web.Application()

    async def respond(request):
        body = await request.json()
        index = len(requests)
        requests.append(body)
        if index >= len(rows):
            return web.json_response({"error": "unexpected model call"}, status=422)
        response = rows[index]["response"]
        payload = response.get("json") or response["chunks"][0]
        if (body["model"] != payload["model"]
                or bool(body.get("stream")) != ("chunks" in response)):
            return web.json_response({"error": "unexpected model route"}, status=422)
        if "json" in response:
            return web.json_response(response["json"], headers=response["headers"])
        stream = web.StreamResponse(headers=response["headers"])
        await stream.prepare(request)
        for chunk in response["chunks"]:
            await stream.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await stream.write(b"data: [DONE]\n\n")
        return stream

    model.router.add_post("/v1/chat/completions", respond)
    bridge = web.Application()

    async def read(request):
        return web.json_response({
            "/v1/self": {"primary": "generated-self", "aliases": []},
            "/v1/groups": [{"id": "g1", "members": [["+852111"]]}],
            "/v1/health": {"ok": True},
        }[request.path])

    for path in ("/v1/self", "/v1/groups", "/v1/health"):
        bridge.router.add_get(path, read)
    apps = {"llm.base_url": model, "platforms.signal.base_url": bridge}
    apps.update({f"providers.{name}.base_url": model for name in cfg.providers})
    return apps

