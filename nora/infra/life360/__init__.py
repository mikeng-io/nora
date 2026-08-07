"""Life360's private API, behind the presence collector (design/19 §4).

`Life360Client` is the one public entry point — auth, token cache, and one
circle read, all degrading to a typed `CircleRead` rather than raising. The
member parsing and `WireState` classification live in `members` (pure, so they
unit-test without a server), and the never-raising HTTP round trip in `http`.
"""

from nora.infra.life360.client import (
    CircleRead,
    DiscoveredCircle,
    DiscoveredMember,
    Life360Client,
    Life360DiscoveryError,
    PlacesRead,
    ReadStatus,
)
from nora.infra.life360.members import Member, parse_members
from nora.infra.life360.places import parse_places

__all__ = [
    "CircleRead",
    "DiscoveredCircle",
    "DiscoveredMember",
    "Life360Client",
    "Life360DiscoveryError",
    "Member",
    "PlacesRead",
    "ReadStatus",
    "parse_members",
    "parse_places",
]
