from __future__ import annotations

from nora.agent.toolkit.spec import _spec


def build(deps):
    from nora.social.adapters.tools.stream import _make_social_read

    social_read = _make_social_read(deps)
    from nora.social.adapters.tools.web import _make_web_search

    web_search = _make_web_search(deps)
    from nora.social.adapters.tools.web import _make_web_fetch

    web_fetch = _make_web_fetch(deps)
    from nora.social.adapters.tools.networks import _make_x_read

    x_read = _make_x_read(deps)
    from nora.social.adapters.tools.networks import _make_x_search

    x_search = _make_x_search(deps)
    from nora.social.adapters.tools.networks import _make_linkedin_read

    linkedin_read = _make_linkedin_read(deps)
    from nora.social.adapters.tools.networks import _make_linkedin_search

    linkedin_search = _make_linkedin_search(deps)
    from nora.social.adapters.tools.instagram import _make_instagram_read

    instagram_read = _make_instagram_read(deps)
    from nora.social.adapters.tools.instagram import _make_instagram_search

    instagram_search = _make_instagram_search(deps)
    return (
        _spec("social_read", social_read),
        _spec("web_search", web_search),
        _spec("web_fetch", web_fetch),
        _spec("x_read", x_read),
        _spec("x_search", x_search),
        _spec("linkedin_read", linkedin_read),
        _spec("linkedin_search", linkedin_search),
        _spec("instagram_read", instagram_read),
        _spec("instagram_search", instagram_search),
    )
