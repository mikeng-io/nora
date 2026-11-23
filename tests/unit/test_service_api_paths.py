"""A route path is checked against the decidable part of the path grammar."""

from __future__ import annotations

import pytest

from nora.domain.service_api import path_conforms


@pytest.mark.parametrize(
    "path",
    [
        "/health", "/ready", "/metrics",
        "/v1/graph/items",
        "/v1/documents/documents/{document_id}",
        "/v1/management/identities/{identity_id}/grants",
        "/v2/platform/self",
        "/v1/platform/channel-members",
        "/v1/intake/operations",
        "/v1/voice/operations/{operation_id}",
        "/v1/graph/queries",
        "/v1/platform/blobs/{key}",
        "/v10/operations/operations",
    ],
)
def test_conforming_paths_are_accepted(path):
    assert path_conforms(path)


@pytest.mark.parametrize(
    "path",
    [
        # root probes are exact and unversioned
        "/health/", "/v1/health", "/healthz", "/health/deep",
        # a version leads
        "/graph/items", "/V1/graph/items", "/v0/graph/items", "/1/graph/items", "/vx/graph/items",
        # then a contract
        "/v1/persons/items", "/v1/Graph/items", "/v1", "/v1/graph",
        # segments are lower-case kebab-case or {param}
        "/v1/graph/Items", "/v1/graph/graph_items", "/v1/graph/items/{Id}",
        "/v1/graph/items/", "/v1//items", "v1/graph/items", "/v1/graph/items?limit=1",
        # an id is a placeholder, and at most one sub-collection under it
        "/v1/graph/items/item-1", "/v1/graph/items/{item_id}/links/{link_id}",
        "/v1/graph/items/{item_id}/{link_id}", "/v1/graph/items/links",
        # the operations, queries and blobs forms
        "/v1/graph/operations/op-1", "/v1/graph/operations/{operation_id}/events",
        "/v1/graph/queries/{query_id}", "/v1/graph/blobs", "/v1/graph/blobs/key-1",
        "/v1/graph/blobs/{key}/meta",
    ],
)
def test_nonconforming_paths_are_refused(path):
    assert not path_conforms(path)
