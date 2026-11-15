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
from nora.people.app.management_invocation import HumanManagement as HumanManagement
from nora.people.app.portal_access import PortalScopes as PortalScopes
from nora.people.app.roster_provisioning import RosterProvisioning as RosterProvisioning
