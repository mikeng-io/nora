"""The people area."""
from nora.people import wire as wire
from nora.people.app.authentication import AccountSessions as AccountSessions
from nora.people.app.holder_link import (
    HolderLinkService as HolderLinkService,
)
from nora.people.app.holder_link import (
    HolderLinkView as HolderLinkView,
)
from nora.people.app.holder_link import (
    LinkActionResult as LinkActionResult,
)
from nora.people.app.identity_resolution import IdentityResolver as IdentityResolver
from nora.people.app.managed_profile import ProfileService as ProfileService
from nora.people.app.management_invocation import HumanManagement as HumanManagement
from nora.people.app.onboarding import OnboardingRule as OnboardingRule
from nora.people.app.onboarding import PlatformOnboarding as PlatformOnboarding
from nora.people.app.portal_access import PortalScopes as PortalScopes
from nora.people.app.room_invocation import (
    Clarify as Clarify,
)
from nora.people.app.room_invocation import (
    RoomManagement as RoomManagement,
)
from nora.people.app.room_invocation import (
    RoomRequest as RoomRequest,
)
from nora.people.app.room_invocation import (
    is_room_command as is_room_command,
)
from nora.people.app.room_invocation import (
    parse_room_command as parse_room_command,
)
from nora.people.app.roster_provisioning import RosterProvisioning as RosterProvisioning
