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


def editable_objectives(tmp_path: Path) -> tuple[DreamerInstructions, Path]:
    """A COPY of the shipped tree under `tmp_path`, mtime-reloading.

    For tests that need to simulate an operator editing an objective. Still the
    real `DreamerInstructions` and still the real reload machine — the only
    thing that changes is that the files are writable, which is exactly how
    production behaves on the bind mount. Simulating the edit by patching a
    module constant, the way these tests used to, stopped being possible when
    the constants left the code; it is also weaker evidence, because it never
    touched the loader at all.
    """
    import shutil

    root = tmp_path / "dreamer"
    shutil.copytree(SHIPPED_DREAMER, root)
    return (
        DreamerInstructions(root, reload="mtime", strict=True, metrics=Metrics()),
        root,
    )


def append_to_objective(root: Path, name: str, extra: str) -> None:
    """Append a line to one objective body and bump its mtime, the way an
    operator's editor would."""
    import os
    import time

    path = root / "objectives" / f"{name}.md"
    path.write_text(path.read_text(encoding="utf-8") + extra, encoding="utf-8")
    future = time.time() + 2
    os.utime(path, (future, future))
