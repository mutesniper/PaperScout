import json

import pytest
from app.storage import Store
from evaluation.prepare_real import prepare, text_hash
from evaluation.run import Dataset
from test_evaluation import dataset


def test_real_preparation_matches_content_not_local_uuid_and_keeps_history(tmp_path):
    fixture = dataset()
    store = Store(tmp_path / 'library.db')
    for doc in fixture.documents:
        local = doc.model_copy(update={'id': 'local-' + doc.id})
        store.save_reading(local)
    manifest = {'version': 'test', 'description': 'test', 'papers': [
        {'id': d.id, 'title': d.title, 'source_url': 'https://example.org/paper',
         'text_sha256': text_hash(d.model_dump())} for d in fixture.documents],
        'cases': [c.model_dump() for c in fixture.cases]}
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(manifest), encoding='utf-8')
    before = store.path.read_bytes()
    out = tmp_path / 'export.json'
    prepare(store.path, path, out)
    result = Dataset.model_validate_json(out.read_bytes())
    assert len(result.cases) == 9
    assert result.documents[0].id == fixture.documents[0].id
    assert result.documents[0].sections == fixture.documents[0].sections
    assert store.path.read_bytes() == before
    manifest['papers'][0]['text_sha256'] = 'wrong-version'
    path.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='不得静默替换版本'):
        prepare(store.path, path, out)
    assert store.path.read_bytes() == before


def test_missing_library_is_not_created(tmp_path):
    import sqlite3
    path = tmp_path / 'manifest.json'
    path.write_text('{}', encoding='utf-8')
    missing = tmp_path / 'missing.db'
    with pytest.raises(sqlite3.OperationalError):
        prepare(missing, path, tmp_path / 'out.json')
    assert not missing.exists()
