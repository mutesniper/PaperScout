"""Opt-in live source check, not part of the deterministic pytest suite."""
import json
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import create_app

if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as directory:
        with TestClient(create_app(Path(directory) / 'smoke.db')) as client:
            response = client.post('/api/search', json={'plan': {'queries': ['transformer']}, 'limit': 3})
            response.raise_for_status()
            result = response.json()
            print(json.dumps({'sources': result['sources'], 'papers': [{k: p[k] for k in ('title', 'url', 'access')} for p in result['papers']]}, ensure_ascii=False, indent=2))
            assert any(s['status'] == 'ok' for s in result['sources']), 'No live source available'
            assert result['papers'], 'No live papers returned'
