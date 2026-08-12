"""User-Agent based bot/crawler detection for `drop`'s PUBLIC listener only
(§5a's internal listener already has no path to the internet at all —
nothing to reject there; see `hardening.py`, which is what actually wires
this into a route).

**Honest limits, stated here rather than overclaimed elsewhere.** A
User-Agent header is a client-supplied string. Nothing below can stop an
adversarial bot that deliberately spoofs a browser UA to evade this filter —
that is a real, permanent gap, not an oversight. What this DOES stop is the
overwhelming majority of real, unwanted crawl traffic: search indexers, SEO
tools, social-media link-unfurlers, and AI-training crawlers overwhelmingly
identify themselves honestly, because a reputable crawler that lied about its
UA to dodge a block risks a much bigger cost (an outright IP ban from sites
that notice) than it saves by skipping one private link. So this is
defense-in-depth layered under `robots.txt` (advisory only, §1 of the task)
and `X-Robots-Tag` (a compliant crawler's actual off-switch, §2) — a real
reduction in unwanted traffic and accidental indexing, not a security
boundary.
"""

from __future__ import annotations

import re

# Product tokens for crawlers that identify themselves plainly. Matched
# case-insensitively as a SUBSTRING of the whole UA string, since real bot
# UAs commonly wrap their token in a longer string, e.g. Googlebot's actual
# UA is "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)".
_KNOWN_BOT_TOKENS = (
    # Major search engines
    "googlebot",
    "bingbot",
    "slurp",  # Yahoo
    "duckduckbot",
    "baiduspider",
    "yandexbot",
    "sogou",
    # Social-media link-unfurlers (fetch a page once to build a preview card)
    "facebookexternalhit",
    "twitterbot",
    "slackbot",
    "discordbot",
    "telegrambot",
    "whatsapp",
    # SEO / archive crawlers
    "ahrefsbot",
    "semrushbot",
    "mj12bot",
    "dotbot",
    "ia_archiver",  # archive.org's classic UA token
    "archive.org",
    # AI-training / AI-agent crawlers
    "gptbot",
    "chatgpt-user",
    "ccbot",  # Common Crawl
    "claudebot",
    "anthropic-ai",
    "bytespider",  # ByteDance
    "perplexitybot",
    "google-extended",
)

# Generic fallback: catches the long tail of bots/scrapers that don't bother
# with a recognizable product token. Deliberately narrow — four common,
# low-ambiguity crawler-vocabulary substrings, NOT broader words like "agent"
# or "client" that would catch real tools too.
#
# Verified against every UA this service actually needs to keep working:
# curl's default ("curl/8.5.0"), python-requests's default
# ("python-requests/2.31.0"), and aiohttp's default
# ("Python/3.12 aiohttp/3.9.1") — none contain "bot", "spider", "crawl", or
# "scrape", so none of this app's own verification scripts (or, if any future
# Nora-side caller ever hit the PUBLIC listener instead of the internal one
# per §6a — none currently do) would trip this filter. Any internal tooling
# that DOES need to keep working here should carry a real UA string rather
# than an empty one — see the missing-UA note on `is_bot_user_agent` below.
_GENERIC_SUBSTRINGS = ("bot", "spider", "crawl", "scrape")

_KNOWN_BOT_RE = re.compile("|".join(re.escape(t) for t in _KNOWN_BOT_TOKENS), re.IGNORECASE)
_GENERIC_RE = re.compile("|".join(_GENERIC_SUBSTRINGS), re.IGNORECASE)


def is_bot_user_agent(user_agent: str | None) -> bool:
    """True if `user_agent` looks like a bot/crawler/scraper: a known
    product token, a generic bot-shaped substring, or a missing/empty header.

    **On missing/empty specifically:** treated as a reject, not merely a
    flag. A real browser always sends a User-Agent header, and every
    legitimate client this service actually needs (curl, python-requests,
    aiohttp — see module docstring) sends a non-empty default one too, so
    there is no real client this rejects. Leaving it open would also hand an
    adversarial bot a trivial one-line bypass of every pattern above
    (`curl -A ""`). This mirrors CLAUDE.md's own fail-closed measurement
    stance (R-5: silence beats a wrong answer) applied to a signal instead of
    an answer — an unreadable/absent UA is treated as suspicious, not benign.
    """
    if user_agent is None or not user_agent.strip():
        return True
    return bool(_KNOWN_BOT_RE.search(user_agent) or _GENERIC_RE.search(user_agent))


__all__ = ["is_bot_user_agent"]
