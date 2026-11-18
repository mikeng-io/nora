"""The admission command's process boundary — honest exit classes and the
bounded diagnostic vocabulary: generated private sentinels (paths, room
labels/ids, person ids, endpoints, credentials, exception parameters)
never reach stdout, stderr or the log, and a failure is reported as what
it is — refusal, unavailable — never another class.
"""

import os
import subprocess
import sys
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

import aiohttp
import pytest
import sqlalchemy as sa

from nora.domain.managed import (
    Outcome,
    OutcomeStatus,
    complete,
    denied,
    unavailable,
)
from nora.infra.ory import KratosClient
from nora.people.adapters.managed_handoff import KratosAdmission
from nora.people.app.managed_admission import FileAdmission, plan
from nora.testing.managed import (
    FakeAuthorityBackend,
    FakeBindingStore,
    FakeIdentityRegistry,
)
from scripts.managed_identity_handoff import (
    COMPLETE,
    REFUSED,
    RETRYABLE,
    run,
)
from tests.fakes import FakeAdmissionKratos, FakeAdmissionProbe

#: One generated private value, planted everywhere the command sees data:
_SENTINEL = "generated-private-9f7e2c"

IDENTITY = f"""\
[persons.{_SENTINEL}]
name = "Private Person"

[[persons.{_SENTINEL}.signal]]
phone = "+85200999999"
label = "{_SENTINEL}-label"
"""

POLICY = f"""\
default = "deny"

[[conversations]]
platform = "signal"
id = "{_SENTINEL}-room"
unit = "family"
label = "{_SENTINEL}-room-label"
persons = ["{_SENTINEL}"]

[units.family]
workspace = "home"
"""

_TARGET = (
    "--database-url", "generated", "--kratos-admin-url", "generated",
    "--keto-read-url", "generated", "--keto-write-url", "generated",
)


@pytest.fixture
def sources(tmp_path):
    identity_path = tmp_path / "identity.toml"
    policy_path = tmp_path / "policy.toml"
    identity_path.write_text(IDENTITY)
    policy_path.write_text(POLICY)
    return identity_path, policy_path


def _argv(command, identity, policy, *extra):
    return [command, "--identity", str(identity), "--policy", str(policy), *extra]


def _services(store=None, backend=None, kratos=None):
    store = store or FakeBindingStore()
    backend = backend or FakeAuthorityBackend()
    registry = FakeIdentityRegistry()
    kratos = kratos if kratos is not None else FakeAdmissionKratos(registry.identities)

    @asynccontextmanager
    async def factory():
        yield FileAdmission(
            bindings=store, backend=backend,
            kratos=kratos, probe=FakeAdmissionProbe(store),
        )
    return factory, store, backend, registry


def _clean(sentinel, capsys):
    """The sentinel must be absent from BOTH streams the process owns."""
    captured = capsys.readouterr()
    assert sentinel not in captured.out
    assert sentinel not in captured.err
    return captured


# -- the source-only plan -----------------------------------------------------


async def test_source_only_plan_never_reaches_a_target(sources, capsys):
    identity_path, policy_path = sources
    reached = []

    @asynccontextmanager
    async def factory():
        reached.append(True)
        yield None

    rc = await run(_argv("plan", identity_path, policy_path), services=factory)
    out = _clean(_SENTINEL, capsys).out
    assert rc == COMPLETE
    assert not reached                    # the optional plan stayed source-only
    assert "plan file-admission:" in out  # positive control: the bounded counts


async def test_a_partial_target_refuses_instead_of_assessing(sources, capsys):
    """One target flag is a request to check the target — the command
    refuses the incomplete target rather than claim it was assessed."""
    identity_path, policy_path = sources
    reached = []

    @asynccontextmanager
    async def factory():
        reached.append(True)
        yield None

    rc = await run(_argv(
        "plan", identity_path, policy_path, "--kratos-admin-url", "generated",
    ), services=factory)
    captured = _clean(_SENTINEL, capsys)
    assert rc == REFUSED
    assert "refused:" in captured.out
    assert not reached


# -- the fresh-claim preflight ------------------------------------------------


async def test_fresh_target_plan_reports_fresh_and_writes_nothing(sources, capsys):
    identity_path, policy_path = sources
    factory, store, backend, registry = _services()
    rc = await run(_argv("plan", identity_path, policy_path, *_TARGET),
                   services=factory)
    captured = _clean(_SENTINEL, capsys)
    assert rc == COMPLETE and "target: fresh" in captured.out
    assert not store.people and not store.accounts and not store.operations
    assert not store.identity_holds and not backend.tuples
    assert not registry.identities


async def test_fresh_target_plan_refuses_a_present_kratos_correlation(sources, capsys):
    """The same collision read apply runs — a held external_id refuses the
    fresh claim before anything exists on the target."""
    kratos = FakeAdmissionKratos()
    kratos.identities[_SENTINEL] = {
        "id": "kratos-foreign-1", "state": "active", "traits": {},
    }
    factory, store, backend, _ = _services(kratos=kratos)
    identity_path, policy_path = sources
    rc = await run(_argv("plan", identity_path, policy_path, *_TARGET),
                   services=factory)
    captured = _clean(_SENTINEL, capsys)
    assert rc == REFUSED
    assert "refused: kratos-correlation-present" in captured.out
    assert not store.people and not store.operations and not backend.tuples


async def test_fresh_target_plan_is_unavailable_when_kratos_cannot_answer(
    sources, capsys,
):
    """A Kratos outage is unavailable, never fresh — the plan cannot claim
    absence it could not read."""
    down = FakeAdmissionKratos()
    down.down = True
    factory, store, backend, _ = _services(kratos=down)
    identity_path, policy_path = sources
    rc = await run(_argv("plan", identity_path, policy_path, *_TARGET),
                   services=factory)
    captured = _clean(_SENTINEL, capsys)
    assert rc == RETRYABLE
    assert "unavailable:" in captured.out and "target: fresh" not in captured.out
    assert not store.people and not store.operations and not backend.tuples


async def test_fresh_target_plan_never_treats_a_malformed_absence_as_fresh(
    sources, capsys,
):
    """Only an explicit absence answer is fresh — a record-less non-denied
    reply is unavailable, not a clean target."""
    class _Malformed(FakeAdmissionKratos):
        async def read(self, *, external_id):
            return complete(), None

    factory, store, _, _ = _services(kratos=_Malformed())
    identity_path, policy_path = sources
    rc = await run(_argv("plan", identity_path, policy_path, *_TARGET),
                   services=factory)
    captured = _clean(_SENTINEL, capsys)
    assert rc == RETRYABLE
    assert "unavailable: identity-unconfirmed" in captured.out
    assert not store.people and not store.operations


# -- source and argument failures ---------------------------------------------


async def test_an_unreadable_source_is_unavailable_not_a_refusal(sources, capsys):
    _, policy_path = sources
    missing = Path(f"/nonexistent/{_SENTINEL}-identity.toml")
    rc = await run(_argv("plan", missing, policy_path))
    captured = _clean(_SENTINEL, capsys)   # the PATH itself is private
    assert rc == RETRYABLE
    assert "unavailable: unreadable-source" in captured.out


async def test_an_invalid_source_is_a_refusal(sources, capsys):
    identity_path, _ = sources
    identity_path.write_text(f"[[[ {_SENTINEL} unparseable")
    rc = await run(_argv("plan", identity_path, sources[1]))
    captured = _clean(_SENTINEL, capsys)
    assert rc == REFUSED
    assert "refused: invalid-identity-file" in captured.out


async def test_a_prepare_outcome_reports_unavailable_honestly(tmp_path):
    """The library preflight agrees: an unreadable source is the Outcome,
    never a refusal entry."""
    plan_result = plan(tmp_path / "missing-identity.toml", tmp_path / "missing-policy.toml")
    assert isinstance(plan_result, Outcome)
    assert plan_result.status is OutcomeStatus.UNAVAILABLE


async def test_parser_errors_never_echo_private_argv(sources, capsys):
    identity_path, policy_path = sources
    rc = await run(_argv(
        "plan", identity_path, policy_path, f"--{_SENTINEL}-option",
    ))
    captured = _clean(_SENTINEL, capsys)
    assert rc == REFUSED
    assert "refused: invalid-arguments" in captured.err


async def test_a_missing_required_argument_is_a_refusal(sources, capsys):
    identity_path, _ = sources
    rc = await run(["plan", "--identity", str(identity_path)])
    captured = _clean(_SENTINEL, capsys)
    assert rc == REFUSED
    assert "refused: invalid-arguments" in captured.err


async def test_help_is_normal(sources, capsys):
    rc = await run(["--help"])
    captured = capsys.readouterr()
    assert rc == COMPLETE
    assert "plan" in captured.out


# -- the target context boundary ----------------------------------------------


async def test_a_missing_configured_credential_is_unavailable(
    sources, capsys, monkeypatch,
):
    """A named-but-absent credential is unavailable, never refused or
    assumed — raised inside the real factory's setup."""
    monkeypatch.delenv("GENERATED_ORY_KEY", raising=False)
    identity_path, policy_path = sources
    rc = await run(_argv(
        "plan", identity_path, policy_path,
        "--database-url", "postgresql+asyncpg://127.0.0.1:1/generated",
        "--kratos-admin-url", "http://127.0.0.1:1",
        "--keto-read-url", "http://127.0.0.1:1",
        "--keto-write-url", "http://127.0.0.1:1",
        "--kratos-admin-key-env", "GENERATED_ORY_KEY",
    ))   # no services= — the real `_target_services` boundary
    captured = _clean(_SENTINEL, capsys)
    assert rc == RETRYABLE
    assert "unavailable: target-setup" in captured.out


async def test_a_credential_value_never_leaks_through_setup(sources, capsys, monkeypatch):
    """The configured credential flows to the client, the service cannot
    answer, and its value is nowhere in the report."""
    monkeypatch.setenv("GENERATED_ORY_KEY", _SENTINEL)
    identity_path, policy_path = sources
    rc = await run(_argv(
        "plan", identity_path, policy_path,
        "--database-url", "postgresql+asyncpg://127.0.0.1:1/generated",
        "--kratos-admin-url", f"http://{_SENTINEL}.invalid",
        "--keto-read-url", "http://127.0.0.1:1",
        "--keto-write-url", "http://127.0.0.1:1",
        "--kratos-admin-key-env", "GENERATED_ORY_KEY",
    ))
    captured = _clean(_SENTINEL, capsys)   # neither key value nor endpoint
    assert rc == RETRYABLE
    assert "unavailable:" in captured.out


async def test_a_context_entry_failure_is_unavailable(sources, capsys):
    @asynccontextmanager
    async def factory():
        raise RuntimeError(f"generated setup fault {_SENTINEL}")
        yield

    identity_path, policy_path = sources
    rc = await run(_argv("plan", identity_path, policy_path, *_TARGET),
                   services=factory)
    captured = _clean(_SENTINEL, capsys)
    assert rc == RETRYABLE
    assert "unavailable: target-setup" in captured.out


async def test_a_context_exit_failure_is_unavailable(sources, capsys):
    factory, _, _, _ = _services()

    @asynccontextmanager
    async def closing_fault():
        async with factory() as admission:
            yield admission
        raise RuntimeError(f"generated cleanup fault {_SENTINEL}")

    identity_path, policy_path = sources
    rc = await run(_argv("apply", identity_path, policy_path, *_TARGET),
                   services=closing_fault)
    captured = _clean(_SENTINEL, capsys)
    assert rc == RETRYABLE
    assert "unavailable: target-close" in captured.out


async def test_a_committed_import_with_a_cleanup_failure_confirms_on_exact_retry(
    sources, capsys,
):
    """The import committed, then cleanup failed: honestly unavailable —
    not complete — until the SAME command's exact retry reads back the
    durable receipt. History is never rewritten or reapplied."""
    factory, store, _, _ = _services()

    @asynccontextmanager
    async def closing_fault():
        async with factory() as admission:
            yield admission
        raise RuntimeError("generated cleanup fault")

    identity_path, policy_path = sources
    argv = _argv("apply", identity_path, policy_path, *_TARGET)
    first = await run(argv, services=closing_fault)
    captured = _clean(_SENTINEL, capsys)
    assert first == RETRYABLE
    assert "import: complete" not in captured.out
    journal = next(iter(store.operations.values()))
    assert journal.state.value == "complete"   # the commit already landed
    accounts_before = dict(store.accounts)
    second = await run(argv, services=factory)
    captured = _clean(_SENTINEL, capsys)
    assert second == COMPLETE
    assert "import:" in captured.out           # positive control
    assert dict(store.accounts) == accounts_before
    assert len(store.operations) == 1


# -- the bounded outcome surface ------------------------------------------------


@asynccontextmanager
async def _stub(outcome):
    class _Stub:
        async def assess_prepared(self, prepared):
            return outcome

        async def apply_prepared(self, prepared):
            return outcome

    yield _Stub()


@pytest.mark.parametrize("detail", [
    "postgres://private-dsn-7f3a@db.internal/managed",
    "identity-state:corrupted-remote-shape",
    "unsupported subject-set namespace:SecretNamespace",
])
async def test_an_unlisted_reason_detail_is_withheld(sources, capsys, detail):
    """A detail outside the explicit vocabulary — a DSN, a remote state
    token, a foreign namespace — prints as the opaque code, never raw."""
    identity_path, policy_path = sources
    rc = await run(_argv(
        "apply", identity_path, policy_path, *_TARGET,
    ), services=lambda: _stub(unavailable(detail)))
    captured = capsys.readouterr()
    assert rc == RETRYABLE
    assert "unavailable: detail-withheld" in captured.out
    assert detail not in captured.out and detail not in captured.err


async def test_a_private_denial_reason_is_withheld(sources, capsys):
    """Even a refusal's raw detail is untrusted — the code withholds a
    reason it cannot name."""
    identity_path, policy_path = sources
    rc = await run(_argv(
        "plan", identity_path, policy_path, *_TARGET,
    ), services=lambda: _stub(denied(f"room:{_SENTINEL}")))
    captured = _clean(_SENTINEL, capsys)
    assert rc == REFUSED
    assert "refused: detail-withheld" in captured.out


async def test_unsupported_input_names_its_class_never_private_labels(
    sources, capsys, caplog,
):
    """A guild/dynamic input is refused by class — the room label, room id
    and dm person id planted in the file appear in no stream and no log."""
    identity_path, policy_path = sources
    policy_path.write_text(POLICY + f"""
[[conversations]]
platform = "discord"
guild = "999000000000000001"
unit = "family"
label = "{_SENTINEL}-guild-label"
persons = "*"

[dm]
persons = ["{_SENTINEL}"]
""")
    rc = await run(_argv("plan", identity_path, policy_path))
    captured = _clean(_SENTINEL, capsys)
    assert _SENTINEL not in caplog.text
    assert rc == REFUSED
    assert "unsupported-input:" in captured.out


async def test_apply_failure_never_logs_exception_parameters(
    sources, capsys, caplog,
):
    """A real SQL exception carrying generated private parameters becomes
    the bounded 'store-failure' — the log keeps a fixed code, never the
    statement's text or its traceback."""
    class _LeakyProbe(FakeAdmissionProbe):
        async def facts(self):
            raise sa.exc.StatementError(
                "private statement failed", "INSERT INTO managed_person",
                {"identifier": _SENTINEL}, Exception("private detail"))

    store = FakeBindingStore()
    backend = FakeAuthorityBackend()
    registry = FakeIdentityRegistry()

    @asynccontextmanager
    async def factory():
        yield FileAdmission(
            bindings=store, backend=backend,
            kratos=FakeAdmissionKratos(registry.identities),
            probe=_LeakyProbe(store),
        )

    identity_path, policy_path = sources
    rc = await run(_argv("apply", identity_path, policy_path, *_TARGET),
                   services=factory)
    captured = _clean(_SENTINEL, capsys)
    assert _SENTINEL not in caplog.text
    assert "Traceback" not in caplog.text
    assert "file admission failed" in caplog.text    # the bounded diagnostic stays
    assert rc == RETRYABLE
    assert "unavailable: store-failure" in captured.out


async def test_a_real_sql_exception_never_reaches_the_report(
    sources, capsys, caplog,
):
    """The statement's own text and bound params reach neither stream —
    the fixed failure code is the diagnostic."""
    class _SqlLeakyProbe(FakeAdmissionProbe):
        async def facts(self):
            raise sa.exc.StatementError(
                "failed", f"VALUES ('{_SENTINEL}')",
                {"identifier": _SENTINEL}, Exception("x"))

    store = FakeBindingStore()

    @asynccontextmanager
    async def factory():
        yield FileAdmission(
            bindings=store, backend=FakeAuthorityBackend(),
            kratos=FakeAdmissionKratos(), probe=_SqlLeakyProbe(store),
        )

    identity_path, policy_path = sources
    rc = await run(_argv("apply", identity_path, policy_path, *_TARGET),
                   services=factory)
    captured = _clean(_SENTINEL, capsys)
    assert _SENTINEL not in caplog.text
    assert "StatementError" not in caplog.text
    assert rc == RETRYABLE
    assert "unavailable: store-failure" in captured.out


async def test_a_real_transport_failure_reports_unavailable_without_the_endpoint(
    sources, capsys,
):
    """The real Kratos adapter against an unreachable endpoint: the
    command reports a bounded transport code and the URL is nowhere."""
    store = FakeBindingStore()
    backend = FakeAuthorityBackend()
    endpoint = f"http://{_SENTINEL}.invalid"

    @asynccontextmanager
    async def factory():
        async with aiohttp.ClientSession() as session:
            yield FileAdmission(
                bindings=store, backend=backend,
                kratos=KratosAdmission(KratosClient(
                    session=session, admin_url=endpoint, timeout_seconds=2.0)),
                probe=FakeAdmissionProbe(store),
            )

    identity_path, policy_path = sources
    rc = await run(_argv("plan", identity_path, policy_path, *_TARGET),
                   services=factory)
    captured = _clean(_SENTINEL, capsys)
    assert rc == RETRYABLE
    assert "unavailable: transport-unavailable" in captured.out  # bounded, endpoint-free


# -- the real process boundary --------------------------------------------------


def _subprocess(argv, cwd):
    return subprocess.run(  # noqa: S603 — this interpreter, this repo's script
        [sys.executable, "scripts/managed_identity_handoff.py", *argv],
        capture_output=True, text=True, cwd=cwd,
        env={**os.environ, "PYTHONPATH": str(cwd)},
    )


def test_subprocess_refuses_unsupported_without_private_labels(tmp_path):
    identity_path = tmp_path / "identity.toml"
    policy_path = tmp_path / "policy.toml"
    identity_path.write_text(IDENTITY)
    policy_path.write_text(POLICY + f"""
[[conversations]]
platform = "discord"
guild = "999000000000000001"
unit = "family"
label = "{_SENTINEL}-guild-label"
persons = "*"
""")
    root = Path(__file__).parents[2]
    result = _subprocess(
        ["plan", "--identity", str(identity_path), "--policy", str(policy_path)],
        cwd=root)
    assert result.returncode == REFUSED
    assert "unsupported-input:" in result.stdout
    assert _SENTINEL not in result.stdout and _SENTINEL not in result.stderr


def test_subprocess_reports_an_unreadable_source_as_unavailable(tmp_path):
    policy_path = tmp_path / "policy.toml"
    policy_path.write_text(POLICY)
    missing = tmp_path / f"{_SENTINEL}-identity.toml"    # never created
    root = Path(__file__).parents[2]
    result = _subprocess(
        ["plan", "--identity", str(missing), "--policy", str(policy_path)],
        cwd=root)
    assert result.returncode == RETRYABLE
    assert "unavailable: unreadable-source" in result.stdout
    assert _SENTINEL not in result.stdout and _SENTINEL not in result.stderr


def test_subprocess_parser_errors_never_echo_private_argv(tmp_path):
    identity_path = tmp_path / "identity.toml"
    policy_path = tmp_path / "policy.toml"
    identity_path.write_text(IDENTITY)
    policy_path.write_text(POLICY)
    root = Path(__file__).parents[2]
    result = _subprocess(
        ["plan", "--identity", str(identity_path), "--policy", str(policy_path),
         f"--{_SENTINEL}"],
        cwd=root)
    assert result.returncode == REFUSED
    assert _SENTINEL not in result.stdout and _SENTINEL not in result.stderr


def test_subprocess_help_exits_zero(tmp_path):
    root = Path(__file__).parents[2]
    result = _subprocess(["--help"], cwd=root)
    assert result.returncode == COMPLETE
    assert "plan" in result.stdout


async def test_a_completed_receipt_cannot_disclose_a_person_as_a_count_key(sources, capsys):
    private_person = "generated_private_person"
    identity, policy = sources
    identity.write_text(IDENTITY.replace(_SENTINEL, private_person))
    policy.write_text(POLICY.replace(_SENTINEL, private_person))
    factory, store, _, _ = _services()
    argv = _argv("apply", identity, policy, *_TARGET)
    assert await run(argv, services=factory) == COMPLETE
    capsys.readouterr()
    record = next(iter(store.operations.values()))
    store.operations[record.operation_id] = replace(record, detail=f"{private_person}=1")
    assert await run(argv, services=factory) == COMPLETE
    captured = capsys.readouterr()
    assert private_person not in captured.out
    assert private_person not in captured.err
    assert "import: complete" in captured.out


@pytest.mark.parametrize("detail", ["transport:GeneratedPrivatePerson", "private_generated_id=123"])
async def test_shape_matching_details_are_never_printed_as_values(sources, capsys, detail):
    identity, policy = sources
    assert await run(_argv("apply", identity, policy, *_TARGET),
                     services=lambda: _stub(unavailable(detail))) == RETRYABLE
    captured = capsys.readouterr()
    assert detail not in captured.out and detail not in captured.err
    assert "unavailable:" in captured.out


async def test_a_denial_without_explicit_absence_cannot_make_a_fresh_plan(sources, capsys):
    class DeniedRead(FakeAdmissionKratos):
        async def read(self, *, external_id):
            return denied("permission-denied"), None

    factory, store, backend, _ = _services(kratos=DeniedRead())
    identity, policy = sources
    assert await run(_argv("plan", identity, policy, *_TARGET), services=factory) == RETRYABLE
    assert "target: fresh" not in capsys.readouterr().out
    assert not store.operations and not store.people and not backend.tuples
    good, _, _, _ = _services()
    assert await run(_argv("plan", identity, policy, *_TARGET), services=good) == COMPLETE


async def test_private_copy_read_io_failure_is_unavailable(sources, capsys, monkeypatch):
    original_open = Path.open

    def fault(path, mode="r", *args, **kwargs):
        if path.parent.name.startswith("nora-admission-") and mode == "rb":
            raise OSError("generated-private-file-read")
        return original_open(path, mode, *args, **kwargs)

    identity, policy = sources
    monkeypatch.setattr(Path, "open", fault)
    assert await run(_argv("plan", identity, policy)) == RETRYABLE
    captured = capsys.readouterr()
    assert "unavailable: unreadable-source" in captured.out
    assert "generated-private-file-read" not in captured.out + captured.err
    monkeypatch.setattr(Path, "open", original_open)
    assert await run(_argv("plan", identity, policy)) == COMPLETE
