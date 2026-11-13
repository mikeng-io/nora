from dataclasses import dataclass
from typing import Any

from nora.domain.ports import SocialProvider
from nora.social.adapters.tools import build as _build

"""Compose this area's tool adapters from shared dependencies."""


def tool_specs(deps):
    return _build(deps)


@dataclass(frozen=True)
class ClientsWiring:
    social_client: Any
    social_subjects: Any


def clients(allowlist, cfg, persons) -> ClientsWiring:
    # `x_read`, `x_search` (DEC-201), `instagram_read` and `instagram_search` get ONE
    # `SocialProvider` — `SocialRouter` — and the registry stays unaware that there are two
    # backends under it. Each backend is wired independently and there is NO credential on…
    social_client: SocialProvider | None = None
    opencli_client = None
    if cfg.social.enabled:
        from nora.infra.social import OpenCliBridgeClient

        opencli_client = OpenCliBridgeClient(
            bridge_url=cfg.social.bridge_url,
            timeout_seconds=cfg.social.timeout_seconds,
            content_max_chars=cfg.social.content_max_chars,
        )
    # The INSTAGRAM backend serves every Instagram read the bridge has a route for —
    # `instagram/story` and `instagram/profile`, `instagram_read`'s two, `instagram/post`,
    # its read of a pasted post or reel link, and `instagram/search`, `instagram_search`'s,…
    instagram_social = None
    social_subjects = None
    if cfg.social.instagram.enabled:
        from nora.infra.social import InstagramSocialClient, PersonsSubjectIndex

        instagram_social = InstagramSocialClient(
            base_url=cfg.social.instagram.base_url,
            timeout_seconds=cfg.social.instagram.timeout_seconds,
            content_max_chars=cfg.social.content_max_chars,
        )
        social_subjects = PersonsSubjectIndex(persons, allowlist)
    if opencli_client is not None or instagram_social is not None:
        from nora.infra.social import EXCEPTION_ROUTES, SocialRouter

        social_client = SocialRouter(
            default=opencli_client,
            exceptions=(
                dict.fromkeys(EXCEPTION_ROUTES, instagram_social)
                if instagram_social is not None
                else {}
            ),
            subjects=social_subjects,
            # A typed handle or a pasted link names a USERNAME, and the bridge's exact lookup is the
            # only thing that turns one into an igid. The same client: the lookup is a GET on the
            # same bridge.
            accounts=instagram_social,
        )
    return ClientsWiring(social_client=social_client, social_subjects=social_subjects)
