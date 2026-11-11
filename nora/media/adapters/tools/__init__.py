from __future__ import annotations

from nora.agent.toolkit.spec import _spec


def build(deps):
    from nora.media.adapters.tools.fetch import _make_media_fetch

    media_fetch = _make_media_fetch(deps)
    from nora.media.adapters.tools.create import _make_generate_image

    generate_image = _make_generate_image(deps)
    from nora.media.adapters.tools.video import _make_video_create

    video_create = _make_video_create(deps)
    from nora.media.adapters.tools.search import _make_media_search

    media_search = _make_media_search(deps)
    from nora.media.adapters.tools.search import _make_media_send

    media_send = _make_media_send(deps)
    generate_image.__doc__ = (generate_image.__doc__ or "").replace(
        "{reference_limit}",
        (f", at most {deps.image_generator.max_reference_images} in one call")
        if deps.image_generator is not None
        else "",
    )
    return (
        _spec("media_fetch", media_fetch),
        _spec("generate_image", generate_image),
        _spec("video_create", video_create),
        _spec("media_search", media_search),
        _spec("media_send", media_send),
    )
