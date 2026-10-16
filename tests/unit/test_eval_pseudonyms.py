"""A pseudonymised case resolves against the deployment through a private map.

Every value here is SYNTHETIC: the real map lives on the deploy host only and
never enters the tree. The pseudonyms play the scrub's part (`grp-pseudo`,
`miles_novak`, `Miles`), the "real" values are invented stand-ins.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from strands_evals import Case

from evals import backbone, pseudonyms
from evals.scenarios import load_corpus
from nora.subject.obtain import RoomNotInPolicyError, SubjectSpec, build_config

_MAP = (
    "grp-pseudo==>grp-synthreal\n"
    "miles_novak==>synth_operator\n"
    "Miles==>Synthop\n"
    "Avery Stone==>Quill Marsh\n"
    "Avery==>Quill\n"
    "@noraexample==>@synth.handle\n"
)

_CORPUS = """
- name: Miles asks in grp-pseudo
  entry: tag
  expect: reply
  input: "Miles asked Avery Stone, then Avery, then Milesy and Ann"
  expected_output: she tells Miles
  metadata:
    speakers: [Miles, Avery]
    note: {who: Avery Stone}
  snapshot:
    platform: signal
    conversation_id: grp-pseudo
    before_ms: 1752000000000
"""

#: The deployment's policy names the REAL room only, as norty's does.
_POLICY = (
    'default = "deny"\n[[conversations]]\nplatform = "signal"\n'
    'id = "grp-synthreal"\nlabel = "synth"\npersons = "*"\nunit = "nora"\n'
)


@pytest.fixture
def corpus(tmp_path, monkeypatch) -> str:
    (tmp_path / "probe.yaml").write_text(_CORPUS, encoding="utf-8")
    monkeypatch.setattr("evals.scenarios._CORPORA", tmp_path)
    monkeypatch.delenv(pseudonyms.ENV, raising=False)
    return "probe"


@pytest.fixture
def mapped(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "private" / "eval-pseudonyms.txt"
    path.parent.mkdir()
    path.write_text(_MAP, encoding="utf-8")
    monkeypatch.setenv(pseudonyms.ENV, str(path))
    return path


def _room(case: Case) -> tuple[str, str]:
    shot = case.metadata["snapshot"]
    return str(shot["platform"]), str(shot["conversation_id"])


def _compose(tmp_path: Path, monkeypatch, room: tuple[str, str]) -> None:
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    build_config(SubjectSpec(
        database_url="postgresql+asyncpg://x/x", workdir=tmp_path / "subject",
        room=room, policy=_POLICY,
    ))


def test_a_pseudonymised_anchor_resolves_with_the_map(corpus, mapped, tmp_path, monkeypatch):
    [case] = load_corpus(corpus)
    assert _room(case) == ("signal", "grp-synthreal")
    _compose(tmp_path, monkeypatch, _room(case))  # the policy names it: no refusal


def test_without_the_map_the_anchor_refuses_exactly_as_before(corpus, tmp_path, monkeypatch):
    [case] = load_corpus(corpus)
    assert _room(case) == ("signal", "grp-pseudo")
    assert not pseudonyms.active()
    with pytest.raises(RoomNotInPolicyError, match="names no conversation"):
        _compose(tmp_path, monkeypatch, _room(case))


def test_a_map_variable_naming_a_missing_file_refuses(corpus, tmp_path, monkeypatch):
    """Set but absent is a typo: it refuses instead of running as if no map."""
    monkeypatch.setenv(pseudonyms.ENV, str(tmp_path / "absent.txt"))
    with pytest.raises(ValueError, match="does not exist"):
        load_corpus(corpus)


def test_a_name_glued_to_cantonese_is_translated(mapped):
    """`\\w` counts CJK as a word character; the corpora write 阿<name>而."""
    assert pseudonyms.resolve("阿Miles而家 Miles今日 Avery") == "阿Synthop而家 Synthop今日 Quill"


def test_map_lines_tolerate_crlf_and_padding(tmp_path, monkeypatch):
    path = tmp_path / "map.txt"
    path.write_bytes(b"Miles ==> Synthop \r\n")
    monkeypatch.setenv(pseudonyms.ENV, str(path))
    assert pseudonyms.resolve("Miles") == "Synthop"


def test_input_speakers_and_expectations_are_translated_but_the_name_is_not(corpus, mapped):
    [case] = load_corpus(corpus)
    assert case.input == "Synthop asked Quill Marsh, then Quill, then Milesy and Ann"
    assert case.expected_output == "she tells Synthop"
    assert case.metadata["speakers"] == ["Synthop", "Quill"]
    assert case.metadata["note"] == {"who": "Quill Marsh"}
    assert case.metadata["person"] == "synth_operator", "the default speaker resolves too"
    assert case.name == "Miles asks in grp-pseudo", "`--case` selects by the name as written"


def test_an_unmapped_or_embedded_token_passes_through_unchanged(mapped):
    assert pseudonyms.resolve("Milesy, Ann and grp-pseudo2") == "Milesy, Ann and grp-pseudo2"
    assert pseudonyms.resolve(7) == 7
    assert pseudonyms.resolve({"miles_novak": "miles_novak"}) == {"miles_novak": "synth_operator"}


def test_the_longest_pseudonym_wins(mapped):
    assert pseudonyms.resolve("Avery Stone and Avery") == "Quill Marsh and Quill"


def test_the_eval_path_constants_resolve_through_the_same_map(mapped, monkeypatch):
    seen: dict[str, Any] = {}
    monkeypatch.setattr(backbone, "hold_transports", lambda _s, **kw: seen.update(kw))
    subject = SimpleNamespace(deps=SimpleNamespace(self_presence=object()))
    spec = SimpleNamespace(deployment_config="", policy=None)
    backbone._hold_the_transport_tier(subject, spec, {})
    assert seen["live_handles"]["instagram"] == "@synth.handle"
    bare = Case(name="c", input="x", metadata={"snapshot": {}})
    spec = backbone.Spec(source_url="x", entry="tag")
    nora_case = backbone._as_nora_case(bare, spec, trigger="t")
    assert nora_case.person == "synth_operator"


def test_the_map_is_never_printed(mapped, capsys, tmp_path, monkeypatch):
    line = pseudonyms.status()
    assert "6 rule(s)" in line
    for rule in _MAP.splitlines():
        for side in rule.split("==>"):
            assert side not in line
    bad = tmp_path / "bad.txt"
    bad.write_text("grp-pseudo==>grp-synthreal\nsecret-real-value\n", encoding="utf-8")
    monkeypatch.setenv(pseudonyms.ENV, str(bad))
    with pytest.raises(ValueError, match="line 2") as exc:
        pseudonyms.resolve("x")
    assert "secret-real-value" not in str(exc.value)
    assert capsys.readouterr() == ("", "")


async def _refusal(monkeypatch, refuse: Any = None) -> str:
    async def _default(_spec: Any) -> Any:
        raise RoomNotInPolicyError("policy names no conversation for the subject's room")
    refuse = refuse or _default

    class _Sandbox:
        def __init__(self, *_: Any, **__: Any) -> None: ...
        async def __aenter__(self) -> str:
            return "postgresql://sb"
        async def __aexit__(self, *_: Any) -> None:
            return None

    monkeypatch.setattr(backbone, "obtain", refuse)
    monkeypatch.setattr(backbone, "sandbox_database", _Sandbox)
    case = Case(name="c", input="x", metadata={
        "snapshot": {"platform": "signal", "conversation_id": "grp-pseudo", "before_ms": 1},
    })
    spec = backbone.Spec(source_url="x", entry="tag", identity="", policy=_POLICY)
    with pytest.raises(RoomNotInPolicyError) as exc:
        await backbone._drive_draw(case, spec, memory_workspace="w")
    return str(exc.value)


async def test_a_refusal_without_the_map_names_the_variable(monkeypatch):
    monkeypatch.delenv(pseudonyms.ENV, raising=False)
    message = await _refusal(monkeypatch)
    assert pseudonyms.ENV in message and "#776" in message


async def test_a_refusal_with_the_map_in_effect_is_not_blamed_on_it(mapped, monkeypatch):
    message = await _refusal(monkeypatch)
    assert pseudonyms.ENV not in message


async def test_a_refusal_with_the_map_in_effect_withholds_the_room(mapped, monkeypatch):
    """With a map the room in the refusal would be a REAL id, and eval logs are
    read and quoted: the message withholds it."""
    async def refuse(_spec):
        raise RoomNotInPolicyError("policy names no conversation for ('signal', 'grp-synthreal')")
    message = await _refusal(monkeypatch, refuse)
    assert "grp-synthreal" not in message and "withheld" in message
