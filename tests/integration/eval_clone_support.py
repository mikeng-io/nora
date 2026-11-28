"""The generated eval clone, its policy and identity, and one finite recorded world."""
from contextlib import contextmanager

import psycopg

from nora.subject.recorded_services import RecordedServices

POLICY = ('default = "deny"\n[[conversations]]\nplatform = "signal"\n'
          'id = "generated-room"\nlabel = "generated-room"\npersons = "*"\n'
          'unit = "generated-unit"\nhome_room = true\n')
IDENTITY = ('[persons.miles_novak]\nname = "Miles"\n'
            '[[persons.miles_novak.signal]]\nphone = "+852111"\n')
ROOM = ("signal", "generated-room")


@contextmanager
def _eval_database(pg_admin, pg_template, name):
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as admin:
        admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()", (pg_template,))
        admin.execute(f'CREATE DATABASE "{name}" TEMPLATE "{pg_template}"')
    try:
        yield (f"postgresql+asyncpg://{pg_admin.user}:{pg_admin.password}"
               f"@{pg_admin.host}:{pg_admin.port}/{name}")
    finally:
        with psycopg.connect(
                pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()", (name,))
            admin.execute(f'DROP DATABASE "{name}"')


def _recording(cfg) -> RecordedServices:
    """The finite external world for one confirmation; each call answered once."""
    bindings = {"llm.base_url": "model", "platforms.signal.base_url": "bridge"}
    bindings.update(
        {f"providers.{n}.base_url": "model" for n in cfg.providers})
    return RecordedServices({"version": 1, "bindings": bindings,
        "services": {
            "bridge": [
                {"method": "POST", "path": "/api/v1/rpc",
                 "response": {"status": 200, "json": [
                     {"id": "nora-self-contacts", "result": [
                         {"number": "generated-account",
                          "uuid": "generated-aci"}]},
                     {"id": "nora-self-status", "result": [
                         {"number": "generated-account",
                          "uuid": "generated-pni"}]}]}},
                *[{"method": "POST", "path": "/api/v1/rpc",
                   "response": {"status": 200, "json": {
                       "id": "nora-listGroups", "result": [
                           {"id": "generated-room", "name": "generated",
                            "isMember": True, "isBlocked": False,
                            "members": [{"number": "+852111",
                                         "uuid": "generated-member"}]}]}}}
                  for _ in range(2)],
            ],
            "model": [{
                "method": "POST", "path": "/v1/chat/completions",
                "response": {"status": 200, "json": {
                    "id": "generated-completion", "model": cfg.llm.model,
                    "created": 1, "object": "chat.completion",
                    "choices": [{"index": 0, "finish_reason": "stop",
                        "message": {"role": "assistant",
                                    "content": '{"match": true}'}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2,
                              "total_tokens": 12}}},
            }],
        }})
