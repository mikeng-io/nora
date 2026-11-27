"""Every row of `docs/design/decisions.md` has a Design link that resolves and a Test
cell that is a real test or says `NOT ENFORCED`."""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "docs" / "design" / "decisions.md"

_ROW = re.compile(r"^\|\s*((?:DEC|R|D|A)-?\d+[a-z]?)\s*\|")
_LINK = re.compile(r"^\[[^\]]+\]\(([^)#]+)(?:#[^)]*)?\)$")
_TEST = re.compile(r"^`([^`:]+?)(?:::([A-Za-z0-9_:]+))?`$")


def _cells(line: str) -> list[str]:
    return [c.strip() for c in re.split(r"(?<!\\)\|", line.strip())[1:-1]]


def _qualified_names(path: Path) -> set[str]:
    names: set[str] = set()

    def walk(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                names.add(prefix + node.name)
                if isinstance(node, ast.ClassDef):
                    walk(node.body, prefix + node.name + "::")

    walk(ast.parse(path.read_text(encoding="utf-8")).body, "")
    return names


def problems(markdown: str, root: Path) -> list[str]:
    """One message per row whose Design link, Test cell or ADR link does not hold."""
    out: list[str] = []
    design_dir = root / "docs" / "design"
    for line in markdown.splitlines():
        m = _ROW.match(line)
        if not m:
            continue
        row, cells = m.group(1), _cells(line)
        if len(cells) != 5:
            out.append(f"{row}: {len(cells)} cells, expected ID | Rule | Design | Test | ADR")
            continue
        _, _, design, test, adr = cells
        link = _LINK.match(design)
        if not link or not (design_dir / link.group(1)).resolve().is_file():
            out.append(f"{row}: Design link {design!r} does not resolve")
        if adr != "—":
            adr_link = _LINK.match(adr)
            if not adr_link or not (design_dir / adr_link.group(1)).resolve().is_file():
                out.append(f"{row}: ADR link {adr!r} does not resolve")
        if test == "NOT ENFORCED":
            continue
        cited = _TEST.match(test)
        if not cited or not (root / cited.group(1)).is_file():
            out.append(f"{row}: Test {test!r} is not a real test file or NOT ENFORCED")
        elif (
            cited.group(2) and cited.group(1).endswith(".py")
            and cited.group(2) not in _qualified_names(root / cited.group(1))
        ):
            out.append(f"{row}: Test {test!r} names a test that does not exist")
    return out


def test_every_decision_row_resolves() -> None:
    markdown = INDEX.read_text(encoding="utf-8")
    rows = re.findall(r"(?m)^\| (?:DEC|R|D|A)-?\d", markdown)
    assert len(rows) >= 200, "row pattern stopped matching"
    assert problems(markdown, ROOT) == []


def test_each_kind_of_broken_row_is_caught(tmp_path: Path) -> None:
    (tmp_path / "docs" / "design").mkdir(parents=True)
    (tmp_path / "docs" / "design" / "a.md").write_text("x")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text(
        "class TestK:\n    def test_a(self): ...\ndef test_b(): ...\n"
    )
    ok = "| R-1 | Rule. | [a](a.md) | `tests/test_x.py::test_b` | — |"
    assert problems(ok, tmp_path) == []
    nested = ok.replace("`tests/test_x.py::test_b`", "`tests/test_x.py::TestK::test_a`")
    assert problems(nested, tmp_path) == []
    assert problems(ok.replace("`tests/test_x.py::test_b`", "NOT ENFORCED"), tmp_path) == []
    for broken in (
        ok.replace("a.md", "gone.md"),
        ok.replace("test_b", "test_gone"),
        ok.replace("tests/test_x.py", "tests/test_gone.py"),
        ok.replace("`tests/test_x.py::test_b`", "see the tests"),
        ok.replace("— |", "[1](../adr/gone.md) |"),
        "| R-1 | Rule. | [a](a.md) | NOT ENFORCED |",
    ):
        assert problems(broken, tmp_path), broken
