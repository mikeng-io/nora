"""Check the committed permission contract against a disposable local Keto.

Pass its read/write URLs explicitly. This writes generated relationships and
removes them afterwards; it never provisions a service or uses live identities.
"""

from __future__ import annotations

import argparse
import json
from http.client import HTTPConnection
from typing import Any, cast
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

from nora.domain.management import ACCESS_PERMITS, MANAGEMENT_PERMITS


def check_policy(read_url: str, write_url: str) -> int:
    for url in (read_url, write_url):
        if urlsplit(url).scheme != "http" or urlsplit(url).hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError("the contract check requires a disposable local Keto")
    prefix = f"contract-{uuid4().hex}"
    person = f"{prefix}-person"
    tuples: list[dict[str, Any]] = []
    checks = 0

    def call(base: str, path: str, method: str = "GET", body=None):
        data = None if body is None else json.dumps(body).encode()
        address = urlsplit(base)
        connection = HTTPConnection(cast(str, address.hostname), address.port, timeout=10)
        try:
            connection.request(
                method, path, body=data, headers={"Content-Type": "application/json"}
            )
            response = connection.getresponse()
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
        finally:
            connection.close()

    def put(namespace: str, obj: str, relation: str, **subject):
        row = dict(namespace=namespace, object=obj, relation=relation, **subject)
        status, body = call(write_url, "/admin/relation-tuples", "PUT", row)
        if status != 201:
            raise AssertionError((row, status, body))
        tuples.append(row)

    def expect(namespace: str, obj: str, permit: str, allowed: bool):
        nonlocal checks
        query = {"namespace": namespace, "object": obj, "relation": permit, "subject_id": person}
        status, body = call(read_url, "/relation-tuples/check?" + urlencode(query))
        if (status, body.get("allowed")) != (200 if allowed else 403, allowed):
            raise AssertionError((namespace, permit, allowed, status, body))
        checks += 1

    permits = sorted(ACCESS_PERMITS | MANAGEMENT_PERMITS)
    try:
        for namespace in ("Workspace", "Unit", "Room"):
            # All accepted scope/permit pairs exist. Each deny affects only its
            # own permit; a direct computed-permit tuple cannot bypass it.
            for permit in permits:
                obj = f"{prefix}-{namespace}-{permit}"
                for granted in permits:
                    put(namespace, obj, f"grant_{granted}", subject_id=person)
                expect(namespace, obj, permit, True)
                put(namespace, obj, f"deny_{permit}", subject_id=person)
                put(namespace, obj, permit, subject_id=person)
                expect(namespace, obj, permit, False)
                for other in permits:
                    if other != permit:
                        expect(namespace, obj, other, True)
                put(namespace, obj, "restricted", subject_id=person)
                for other in permits:
                    expect(namespace, obj, other, False)
        workspace, unit, room = (f"{prefix}-{kind}" for kind in ("w", "u", "r"))
        put(
            "Unit",
            unit,
            "workspace",
            subject_set={"namespace": "Workspace", "object": workspace, "relation": ""},
        )
        put("Room", room, "unit", subject_set={"namespace": "Unit", "object": unit, "relation": ""})
        for permit in ("speak", "view"):
            put("Workspace", workspace, f"grant_{permit}", subject_id=person)
            expect("Room", room, permit, True)
        put("Workspace", workspace, "deny_speak", subject_id=person)
        expect("Room", room, "speak", False)
        expect("Room", room, "view", True)
        return checks
    finally:
        for row in reversed(tuples):
            query = dict(row)
            subject = query.pop("subject_set", None)
            if subject:
                query.update({f"subject_set.{key}": value for key, value in subject.items()})
            status, body = call(write_url, "/admin/relation-tuples?" + urlencode(query), "DELETE")
            if status != 204:
                raise AssertionError(("cleanup", status, body))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-url", required=True)
    parser.add_argument("--write-url", required=True)
    args = parser.parse_args()
    print(f"Keto permission checks passed: {check_policy(args.read_url, args.write_url)}")
