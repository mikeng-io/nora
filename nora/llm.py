"""OmniRoute (OpenAI-compatible) client."""

from openai import AsyncOpenAI

from .persona import SYSTEM_PROMPT


class LLM:
    def __init__(self, base_url: str, api_key: str, model: str, max_tokens: int, temperature: float):
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature

    async def reply(self, context: str) -> str:
        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": context},
            ],
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )
        return (resp.choices[0].message.content or "").strip()
