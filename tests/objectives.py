"""The shipped Dreamer instruction store, for tests that need a real one.

Not a fake. An adaptor takes an `objective` collaborator that reads a body by
name, and the real `nora.instructions.dreamer.DreamerInstructions` over the
real `nora/dreamer/` tree is both cheaper to build than a double and strictly
better evidence: a test using it exercises the production text, so a broken
objective file fails the unit suite rather than the next Dreamer tick.

`reload="startup"` because a test does not edit the shipped tree — tests that DO
need to simulate an operator edit build their own `DreamerInstructions` over
`tmp_path`, which is still the real class and still the real reload machine.
"""

from pathlib import Path

from nora.instructions.dreamer import DreamerInstructions
from nora.obs.metrics import Metrics

ROOT = Path(__file__).resolve().parents[1]
SHIPPED_DREAMER = ROOT / "nora" / "dreamer"


def shipped_objectives() -> DreamerInstructions:
    return DreamerInstructions(
        SHIPPED_DREAMER, reload="startup", strict=True, metrics=Metrics(),
    )


