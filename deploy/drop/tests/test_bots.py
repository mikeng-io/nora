"""Unit tests for `bots.is_bot_user_agent` — the pattern list itself, in
isolation from the ASGI wiring `hardening.py`/`server.py` build on top of it.
`tests/test_public_hardening.py` covers the end-to-end 403/passthrough
behaviour; this file pins the matching logic precisely enough that a future
edit to the pattern lists can't silently narrow or widen them.
"""

from __future__ import annotations

import pytest

from bots import is_bot_user_agent

# One representative UA per category named in the task, using each crawler's
# REAL published UA string (not just the bare token) -- these are the shape
# is_bot_user_agent actually has to match in production, where the token is
# wrapped inside a longer "Mozilla/5.0 (compatible; ...)"-style string.
_REAL_BOT_UAS = [
    # Major search engines
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
    "Mozilla/5.0 (compatible; Yahoo! Slurp; http://help.yahoo.com/help/us/ysearch/slurp)",
    "DuckDuckBot/1.1; (+http://duckduckgo.com/duckduckbot.html)",
    "Mozilla/5.0 (compatible; Baiduspider/2.0; +http://www.baidu.com/search/spider.html)",
    "Mozilla/5.0 (compatible; YandexBot/3.0; +http://yandex.com/bots)",
    "Sogou web spider/4.0(+http://www.sogou.com/docs/help/webmasters.htm#07)",
    # Social-media link-unfurlers
    "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
    "Twitterbot/1.0",
    "Slackbot-LinkExpanding 1.0 (+https://api.slack.com/robots)",
    "Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)",
    "TelegramBot (like TwitterBot)",
    "WhatsApp/2.23.20.0 A",
    # SEO / archive crawlers
    "Mozilla/5.0 (compatible; AhrefsBot/7.0; +http://ahrefs.com/robot/)",
    "Mozilla/5.0 (compatible; SemrushBot/7~bl; +http://www.semrush.com/bot.html)",
    "Mozilla/5.0 (compatible; MJ12bot/v1.4.8; http://mj12bot.com/)",
    "Mozilla/5.0 (compatible; DotBot/1.2; +https://opensiteexplorer.org/dotbot)",
    "ia_archiver (+http://www.alexa.com/site/help/webmasters)",
    # AI-training crawlers
    "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.2; "
    "+https://openai.com/gptbot",
    "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ChatGPT-User/1.0; "
    "+https://openai.com/bot",
    "CCBot/2.0 (https://commoncrawl.org/faq/)",
    "Mozilla/5.0 (compatible; ClaudeBot/1.0; +claudebot@anthropic.com)",
    "anthropic-ai/1.0",
    "Mozilla/5.0 (Linux; Android 5.0) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Mobile Safari/537.36 (compatible; Bytespider; ...)",
    "Mozilla/5.0 (compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)",
    "Mozilla/5.0 (compatible; Google-Extended)",
    # Generic fallback substrings, wrapped in an otherwise browser-shaped UA
    "SomeRandomThing/1.0 (+webbot; scans sites)",
    "curious-spider-3000/2.1",
    "GenericCrawlerFramework/9.9",
    "unbranded-scrape-tool/0.1",
]


@pytest.mark.parametrize("ua", _REAL_BOT_UAS)
def test_known_bot_and_generic_uas_are_flagged(ua: str) -> None:
    assert is_bot_user_agent(ua) is True


@pytest.mark.parametrize(
    "ua",
    [
        # Ordinary desktop/mobile browsers
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like "
        "Gecko) Version/17.5 Safari/605.1.15",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
        # This service's own tooling / internal clients (module docstring) --
        # none of these may ever be caught by the generic substring fallback.
        "curl/8.5.0",
        "python-requests/2.31.0",
        "Python/3.12 aiohttp/3.9.1",
    ],
)
def test_real_browser_and_internal_tooling_uas_pass(ua: str) -> None:
    assert is_bot_user_agent(ua) is False


def test_missing_user_agent_is_flagged() -> None:
    assert is_bot_user_agent(None) is True


def test_empty_user_agent_is_flagged() -> None:
    assert is_bot_user_agent("") is True


def test_whitespace_only_user_agent_is_flagged() -> None:
    assert is_bot_user_agent("   ") is True


def test_matching_is_case_insensitive() -> None:
    assert is_bot_user_agent("MOZILLA/5.0 GOOGLEBOT/2.1") is True
    assert is_bot_user_agent("some-WEIRD-ScRaPeR/1.0") is True
