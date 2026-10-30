"""One-shot live smoke test for the xAI image-gen lane (DEC-137).

Runs the REAL adapter against the REAL xAI API using the refresh token the
operator minted via `nora xai-login`. This is a deliberate, small,
operator-authorized burn of subscription quota (~$0.02, one image) — NOT a
unit test; it never runs in `make gate`.

Usage:  uv run python scripts/smoke_xai_imagegen.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from nora.secret import credentials

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nora.domain.ports import ImageResult, ImageSize
from nora.infra.image_gen.xai_adapter import XaiImageGenerator
from nora.infra.image_gen.xai_oauth import XaiOAuth


async def main() -> int:
    refresh = credentials().get("XAI_OAUTH_REFRESH_TOKEN", "")
    if not refresh:
        print("XAI_OAUTH_REFRESH_TOKEN env var is empty")
        return 2

    oauth = XaiOAuth(
        refresh_token=refresh,
        state_file=Path("state/xai-oauth-smoke.json"),
    )
    gen = XaiImageGenerator(oauth=oauth, timeout_seconds=120.0)

    # 1. Token lane first (refresh → access token).
    print("1. OAuth access_token() ...")
    tok = await oauth.access_token()
    print(f"   status={tok.status} reason={tok.reason or '-'} token_len={len(tok.access_token)}")

    if tok.status != "ok":
        print("   FAIL: no access token — lane dead")
        return 1

    # 2. One real generation (quota burn, small).
    print("2. generate(prompt='a tiny red pixel-art robot waving') ...")
    result: ImageResult = await gen.generate(
        prompt="a tiny red pixel-art robot waving",
        size=ImageSize(tier="medium", aspect_ratio="1:1"),
    )
    print(f"   status={result.status} reason={result.reason or ''} provider={result.provider}")
    if result.status == "ok" and result.artifact:
        print(f"   artifact={result.artifact[:90]}...")
        print("SMOKE OK")
        return 0
    print(f"   artifact={result.artifact!r}")
    print("SMOKE FAIL")
    return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
