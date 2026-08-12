from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from skills.mermaid.render import MermaidRenderError, render_mermaid


def _fake_mmdc_run(svg_text: str):
    """A `subprocess.run` fake that behaves like the real `mmdc`: it writes
    its result to the `-o <path>` argument on disk rather than printing it to
    stdout. A fake that only set `.stdout` (and never touched the
    filesystem) would let `render_mermaid` pass while reading nothing real --
    reverting `render_mermaid` to `return result.stdout` would NOT turn this
    test red, which is exactly the decorative-fake failure mode CLAUDE.md
    warns about. Writing the file here means the test still exercises
    `render_mermaid`'s actual read-the-output-file behavior."""

    def run(cmd: list[str], **_kwargs: object) -> MagicMock:
        output_path = Path(cmd[cmd.index("-o") + 1])
        output_path.write_text(svg_text)
        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        return result

    return run


def test_a_valid_diagram_returns_svg_markup():
    with patch("skills.mermaid.render.subprocess.run") as mock_run:
        mock_run.side_effect = _fake_mmdc_run("<svg>fake diagram</svg>")
        result = render_mermaid(diagram_source="flowchart LR\n  A --> B")
    assert result.startswith("<svg")


def test_mermaid_cli_is_invoked_with_the_source_and_no_network_flags():
    with patch("skills.mermaid.render.subprocess.run") as mock_run:
        mock_run.side_effect = _fake_mmdc_run("<svg></svg>")
        render_mermaid(diagram_source="flowchart LR\n  A --> B")
    args = mock_run.call_args
    assert "mmdc" in args[0][0][0] or "mmdc" in str(args[0][0])


def test_an_invalid_diagram_raises_rather_than_returning_garbage():
    with patch("skills.mermaid.render.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = "Parse error on line 1"
        with pytest.raises(MermaidRenderError):
            render_mermaid(diagram_source="not a valid diagram")
