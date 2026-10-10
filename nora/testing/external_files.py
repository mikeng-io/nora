"""`FakeExternalFilesSource`: an `ExternalFilesSource` with its files in
memory (design/external-files/00-design.md §4). `recognise` is the domain's
own pure recogniser, so the fake recognises exactly what production does;
the three reads answer from `files` and raise `ExternalFileError` the way an
adapter must. Pinned by `tests/contract/test_external_files_source_contract.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from nora.domain.external_files import (
    ExternalFile,
    ExternalFileError,
    ExternalFileRef,
    FilePart,
    recognise,
)


@dataclass
class FakeFile:
    """One file the fake holds: what `describe` answers, its text parts and
    its bytes. `shared=False` is a file that exists and is not shared with
    her (`refused`)."""

    name: str
    kind: str = "document"
    parts: tuple[str, ...] = ("",)
    data: bytes = b""
    shared: bool = True
    owner: str = ""
    modified_ms: int | None = None


@dataclass
class FakeExternalFilesSource:
    name: str = "google_drive"
    files: dict[str, FakeFile] = field(default_factory=dict)
    #: Set to make every read `unavailable` (no account, a revoked token).
    down: bool = False
    calls: list[tuple[str, str]] = field(default_factory=list)

    def recognise(self, *, url: str) -> ExternalFileRef | None:
        ref = recognise(url)
        return ref if ref is not None and ref.provider == self.name else None

    def _file(self, ref: ExternalFileRef, verb: str) -> FakeFile:
        self.calls.append((verb, ref.file_id))
        if self.down:
            raise ExternalFileError("unavailable", "the provider could not be reached")
        held = self.files.get(ref.file_id)
        if held is None or ref.provider != self.name:
            raise ExternalFileError("nothing_found", "no such file")
        if not held.shared:
            raise ExternalFileError("refused", "not shared with her")
        return held

    async def describe(self, *, ref: ExternalFileRef) -> ExternalFile:
        held = self._file(ref, "describe")
        return ExternalFile(
            ref=ref, name=held.name, kind=held.kind,  # type: ignore[arg-type]
            owner=held.owner, modified_ms=held.modified_ms,
        )

    async def read(self, *, ref: ExternalFileRef, part: int) -> FilePart:
        held = self._file(ref, "read")
        if not 1 <= part <= len(held.parts):
            raise ExternalFileError("nothing_found", f"no part {part} of {len(held.parts)}")
        return FilePart(text=held.parts[part - 1], part=part, parts=len(held.parts))

    async def fetch_bytes(self, *, ref: ExternalFileRef) -> bytes:
        return self._file(ref, "fetch_bytes").data
