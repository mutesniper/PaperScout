import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone, timedelta
from .schemas import SearchResult

class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS searches (id TEXT PRIMARY KEY, created_at TEXT, payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, expires TEXT, payload TEXT)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, result: SearchResult):
        with self.connect() as db:
            db.execute('INSERT INTO searches VALUES (?, ?, ?)', (result.id, result.created_at, result.model_dump_json()))

    def history(self):
        with self.connect() as db:
            rows = db.execute('SELECT payload FROM searches ORDER BY created_at DESC LIMIT 50').fetchall()
        return [dict(id=r['id'], created_at=r['created_at'], queries=r['request']['plan']['queries'], count=len(r['papers'])) for (value,) in rows for r in [json.loads(value)]]

    def get(self, record_id):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM searches WHERE id=?', (record_id,)).fetchone()
        return SearchResult.model_validate_json(row[0]) if row else None

    def cached(self, key):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM cache WHERE key=? AND expires>?', (key, datetime.now(timezone.utc).isoformat())).fetchone()
        return json.loads(row[0]) if row else None

    def cache(self, key, payload):
        expires = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO cache VALUES (?, ?, ?)', (key, expires, json.dumps(payload, ensure_ascii=False)))
