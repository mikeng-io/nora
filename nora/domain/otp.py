"""One-time codes: the closed vocabularies `nora.app.otp` passes and the metrics'
help text reads (the label-vocabulary rule: one constant, not a list written out
twice). Standard library only, like the rest of `nora.domain`."""

from __future__ import annotations

#: Why a code was asked for. `sign_in` is the portal; `adopt` is reserved for
#: confirming somebody before Nora adopts them. The table's CHECK says the same.
PURPOSES: tuple[str, ...] = ("sign_in", "adopt")

#: The platforms a code can be sent on: the two where Nora holds a phone number.
PLATFORMS: tuple[str, ...] = ("signal", "whatsapp")

#: How a request for a code ended. Every one of them answers the caller the
#: same way, so these are the only place the difference is visible.
REQUEST_OUTCOMES: tuple[str, ...] = (
    "sent",          # the code reached the platform
    "send_failed",   # every guard passed and the platform refused it, or a guard refused it
    "unknown",       # the identifier names nobody in identity.toml
    "no_account",    # a known person with no account on that platform taking a DM
    "unavailable",   # that platform is not wired in this process
    "rate_limited",  # this person was sent `max_per_hour` codes in the last hour
)

#: How a check of a code ended.
CHECK_OUTCOMES: tuple[str, ...] = (
    "ok",            # the right code for a live challenge: the person is returned
    "refused",       # wrong, spent, expired, superseded, worn out or unknown
)
