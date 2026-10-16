#!/usr/bin/env python3
"""Build the private PII denylist the pre-commit detector matches against.

The detector (`scripts/pii_scan.py`) knows real values only through a file
OUTSIDE the repo — a denylist of real phone numbers inside a public repository
is the disclosure it exists to prevent. This script writes that file from the
two sources that hold the real values:

- a deployment `identity.toml` (every id kind of every account, the person ids,
  the aliases, and identifier-shaped numbers in its comments), and
- the left-hand side of a scrub replacement map (`lhs==>rhs`, git filter-repo
  `--replace-text` format), which carries what mining the history added:
  group ids, hostnames, employer, CJK aliases nobody would think to look up.

It reads them at run time and contains no values itself. Output: one
`kind<TAB>value` per line, kinds `text`, `phone`, `number`, `uuid`, `hex`,
`b64`; mode 600 in a mode-700 directory. It prints counts, never values.

    python3 scripts/pii_denylist_build.py \\
        --identity <path/to/identity.toml> [--identity ...] \\
        --map <path/to/replacements.txt> [--map ...] \\
        [--exempt <private file of values to drop>] [--keep <public term>] [--keep-file <file>]
        [--out ~/.config/nora/pii-denylist.txt]

Dropped on purpose, and counted: account `label`s (personal/business/live are
not identifiers), project identity (the public org and domain tokens -- they
stay by operator decision; a personal name is never exempt), and single-word names shorter than
`--min-token` letters, which collide with ordinary words (a 3-letter surname
is a substring-word in every licence header). Full names and person ids
keep them covered.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import tempfile
import time

NON_IDENTIFYING_KEYS = {"label"}
# The project's public org and domain tokens only (scripts/pii_scan.py says
# why no personal name may be listed here).
# The bare org stem is derived, not quoted: the scrub map rewrites a quoted
# bare handle, and this set must survive the history rewrite unchanged.
PROJECT_IDENTITY = {"mikeng-io", "mikeng-io".removesuffix("-io"), "mikeng.io", "iam@mikeng.io"}

# Terms the owner has declared PUBLIC: an ordinary name that stays out of the
# denylist even though a private source lists it. The deploy host's short name
# (and its home directory) is an ordinary server name. Matched on the whole
# value's words, so a longer sibling that is still
# sensitive -- `<term>-dev`, a tailnet name, an address -- stays denylisted.
# Add more with --keep / --keep-file (a private file when the term is not
# itself public).
PUBLIC_KEEP = {"norty", "/home/norty/"}


def _words(value: str) -> tuple[str, ...]:
    return tuple(w.lower() for w in re.findall(r"[A-Za-z0-9]+", value))


UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}"
)


def classify(value: str, key: str = "") -> str | None:
    v = value.strip()
    if not v:
        return None
    if UUID_RE.fullmatch(v):
        return "uuid"
    digits = re.sub(r"\D", "", v)
    if re.fullmatch(r"\+?[\d \-().]+", v) and len(digits) >= 7:
        return "phone" if v.startswith("+") or key == "phone" else "number"
    if re.fullmatch(r"[0-9a-f]{8}", v):
        return "hex"
    if (
        len(v) >= 16
        and " " not in v
        and re.search(r"[+/=_-]", v)
        and re.search(r"[A-Z]", v)
        and re.search(r"[a-z]", v)
        and re.search(r"\d", v)
        and "@" not in v
        and "." not in v
    ):
        return "b64"
    return "text"


def from_identity(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for m in re.finditer(r"^\s*\[\[?\s*persons\.([A-Za-z0-9_-]+)", text, re.M):
        out.append((m.group(1), "person_id"))
    for m in re.finditer(r'^\s*([A-Za-z_]+)\s*=\s*"([^"]*)"', text, re.M):
        if m.group(1) not in NON_IDENTIFYING_KEYS:
            out.append((m.group(2), m.group(1)))
    for m in re.finditer(r"^\s*([A-Za-z_]+)\s*=\s*\[([^\]]*)\]", text, re.M):
        for v in re.findall(r'"([^"]*)"', m.group(2)):
            out.append((v, m.group(1)))
    # identifier-shaped values that live only in comments
    for line in text.splitlines():
        if not line.lstrip().startswith("#"):
            continue
        for v in re.findall(r"(?<![\w.])\+?\d{9,20}(?![\w.])", line):
            out.append((v, "comment"))
        for v in UUID_RE.findall(line):
            out.append((v, "comment"))
    return out


_LOOKAROUND = re.compile(r"\(\?<?[!=](?:[^()]|\([^()]*\))*\)")


def from_map(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for line in text.splitlines():
        if "==>" not in line:
            continue
        lhs = line.split("==>", 1)[0]
        if lhs.startswith("regex:"):
            body = _LOOKAROUND.sub("", lhs[len("regex:") :])
            lit = re.match(r"[^\\\[\](){}.*+?|^$]*", body)
            value = lit.group(0) if lit else ""
            if not value:
                continue
            out.append((value, "map-regex"))
            continue
        if lhs.startswith(("glob:", "literal:")):
            lhs = lhs.split(":", 1)[1]
        lhs = re.sub(r"^\\[ntr]", "", lhs).strip().strip('"')
        # the scrub's escaped forms (`\nName`) and prefix forms (`…0100`) are
        # spellings of a value, not values
        lhs = lhs.lstrip("…")
        out.append((lhs, "map"))
    return out


def build(
    sources: list[tuple[str, str]], exempt: set[str], min_token: int, keep: set[str] | None = None
):
    kept: dict[tuple[str, str], None] = {}
    dropped = {
        "project-identity": 0,
        "short-word": 0,
        "exempt": 0,
        "too-short": 0,
        "empty": 0,
        "public-keep": 0,
    }
    keep_words = {_words(k) for k in (PUBLIC_KEEP | (keep or set()))} - {()}
    for value, key in sources:
        v = value.strip()
        if not v:
            dropped["empty"] += 1
            continue
        if v.casefold() in PROJECT_IDENTITY:
            dropped["project-identity"] += 1
            continue
        if _words(v) in keep_words:
            dropped["public-keep"] += 1
            continue
        if v.casefold() in exempt:
            dropped["exempt"] += 1
            continue
        kind = classify(v, key)
        if kind is None:
            continue
        if kind == "text":
            if v.isascii():
                words = re.findall(r"[A-Za-z0-9]+", v)
                if not words:
                    dropped["empty"] += 1
                    continue
                if len(words) == 1 and len(words[0]) < min_token:
                    dropped["short-word"] += 1
                    continue
            elif len(v) < 2:
                dropped["too-short"] += 1
                continue
        kept[(kind, v)] = None
    return list(kept), dropped


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--identity", action="append", default=[], help="an identity.toml (repeatable)")
    ap.add_argument(
        "--map", action="append", default=[], help="a scrub replacement map (repeatable)"
    )
    ap.add_argument("--exempt", help="private file: one value per line to leave out")
    ap.add_argument(
        "--keep",
        action="append",
        default=[],
        help="a public term to keep out of the denylist (whole-value words; repeatable)",
    )
    ap.add_argument("--keep-file", help="private file: one public term per line")
    ap.add_argument(
        "--out",
        default=os.path.expanduser(os.path.join("~", ".config", "nora", "pii-denylist.txt")),
    )
    ap.add_argument("--min-token", type=int, default=5)
    a = ap.parse_args(argv)
    if not a.identity and not a.map:
        ap.error("give at least one --identity or --map")
    sources: list[tuple[str, str]] = []
    provenance = []
    for path in a.identity + a.map:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        provenance.append(
            f"{os.path.basename(path)} sha256:{hashlib.sha256(text.encode()).hexdigest()[:12]}"
        )
        sources += from_identity(text) if path in a.identity else from_map(text)
    exempt: set[str] = set()
    if a.exempt:
        with open(a.exempt, encoding="utf-8") as fh:
            exempt = {x.strip().casefold() for x in fh if x.strip() and not x.startswith("#")}
    keep = set(a.keep)
    if a.keep_file:
        with open(a.keep_file, encoding="utf-8") as fh:
            keep |= {x.strip() for x in fh if x.strip() and not x.startswith("#")}
    entries, dropped = build(sources, exempt, a.min_token, keep)
    counts: dict[str, int] = {}
    for kind, _ in entries:
        counts[kind] = counts.get(kind, 0) + 1
    out_dir = os.path.dirname(os.path.abspath(a.out))
    os.makedirs(out_dir, mode=0o700, exist_ok=True)
    header = [
        "# nora PII denylist — PRIVATE. Never commit, never paste. Regenerate, do not hand-edit.",
        f"# generated {time.strftime('%Y-%m-%dT%H:%M:%S%z')} by scripts/pii_denylist_build.py",
        *[f"# source: {p}" for p in provenance],
        "# kinds: " + ", ".join(f"{k}={n}" for k, n in sorted(counts.items())),
        "# dropped: " + ", ".join(f"{k}={n}" for k, n in sorted(dropped.items())),
    ]
    fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=".pii-denylist.")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(header) + "\n")
        for kind, v in sorted(entries):
            fh.write(f"{kind}\t{v}\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, a.out)
    print(f"wrote {len(entries)} entries to {a.out} (mode 600)")
    print("  kinds:   " + ", ".join(f"{k}={n}" for k, n in sorted(counts.items())))
    print("  dropped: " + ", ".join(f"{k}={n}" for k, n in sorted(dropped.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
