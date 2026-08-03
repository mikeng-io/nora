"""E.1 step 3: bare Agent + OmniRoute returns non-empty text. Network-marked."""

import os

import pytest

pytestmark = pytest.mark.network

BASE_URL = os.environ.get("OMNIROUTE_BASE_URL", "http://localhost:20128/v1")
MODEL = os.environ.get("OMNIROUTE_MODEL", "deepseek-v4-flash")


async def test_bare_agent_reaches_omniroute():
    from openai import AsyncOpenAI
    from strands import Agent
    from strands.models.openai import OpenAIModel

    key = os.environ.get("OMNIROUTE_API_KEY")
    assert key, "OMNIROUTE_API_KEY must be set to run the live smoke test"

    client = AsyncOpenAI(api_key=key, base_url=BASE_URL)
    try:
        model = OpenAIModel(
            model_id=MODEL, client=client, params={"max_tokens": 64, "temperature": 0.0}
        )
        agent = Agent(model=model, system_prompt="Reply with one short word.",
                      callback_handler=None)
        result = await agent.invoke_async("ping")
        assert str(result).strip(), "OmniRoute returned empty text"
    finally:
        await client.close()   # the caller owns the client lifecycle
