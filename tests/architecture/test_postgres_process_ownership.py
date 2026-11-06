"""Repository PostgreSQL services must not adopt external exec-probe children."""
from pathlib import Path

import yaml


def test_postgres_services_have_init():
    found = []
    for path in map(Path, [
        'docker-compose.yml', 'deploy/memory-graph-spike/compose.yaml',
        'deploy/signoz/deployment/compose.yaml',
    ]):
        services = yaml.safe_load(path.read_text())['services']
        for name, service in services.items():
            if service.get('image', '').startswith(('pgvector/', 'postgres:', 'apache/age:')):
                found.append((str(path), name))
                assert service.get('init') is True, (path, name)
    assert len(found) == 4
