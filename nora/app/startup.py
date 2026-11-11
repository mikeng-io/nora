"""The refusal every composition root shares: the boot asked for a state Nora
cannot honestly run, so she refuses rather than starts half-wired."""


class StartupError(Exception):
    """Raised by a `build_*` step (or the wire under it) at boot when a
    configured surface cannot be made to work: the error the CLI prints and
    the reason there is no process to debug afterward."""
