import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone, timedelta
from .schemas import SearchResult
from .reading_schemas import Reading, Summary, DocumentSummary

class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS searches (id TEXT PRIMARY KEY, created_at TEXT, payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, expires TEXT, payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS qa_vectors (key TEXT PRIMARY KEY, payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS questions (id TEXT PRIMARY KEY, reading_id TEXT, created_at TEXT, payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS comparisons (id TEXT PRIMARY KEY, created_at TEXT, payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS agent_runs (id TEXT PRIMARY KEY, created_at TEXT, payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS readings (id TEXT PRIMARY KEY, created_at TEXT, payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS document_summaries (reading_id TEXT PRIMARY KEY, payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS summaries (reading_id TEXT, section_id TEXT, payload TEXT, PRIMARY KEY (reading_id, section_id))')

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

    def save_reading(self, doc: Reading):
        with self.connect() as db:
            db.execute('INSERT INTO readings VALUES (?, ?, ?)', (doc.id, doc.created_at, doc.model_dump_json()))

    def reading(self, reading_id):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM readings WHERE id=?', (reading_id,)).fetchone()
            summaries = db.execute('SELECT payload FROM summaries WHERE reading_id=?', (reading_id,)).fetchall()
            full = db.execute('SELECT payload FROM document_summaries WHERE reading_id=?', (reading_id,)).fetchone()
        if row is None:
            return None
        doc = Reading.model_validate_json(row[0])
        doc.summaries = [Summary.model_validate_json(value) for (value,) in summaries]
        doc.document_summary = DocumentSummary.model_validate_json(full[0]) if full else None
        return doc

    def save_document_summary(self, reading_id, summary: DocumentSummary):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO document_summaries VALUES (?, ?)', (reading_id, summary.model_dump_json()))

    def reading_history(self):
        with self.connect() as db:
            rows = db.execute('SELECT payload FROM readings ORDER BY created_at DESC LIMIT 50').fetchall()
        return [{**{k: data[k] for k in ('id', 'title', 'kind', 'created_at', 'page_count')}, 'source_url': data.get('source_url')}
                for (value,) in rows for data in [json.loads(value)]]

    def save_summary(self, reading_id, summary: Summary):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO summaries VALUES (?, ?, ?)',
                       (reading_id, summary.section_id, summary.model_dump_json()))

    def vector_cache(self, key):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM qa_vectors WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_vector_cache(self, key, vectors):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO qa_vectors VALUES (?, ?)', (key, json.dumps(vectors)))

    def save_question(self, answer):
        with self.connect() as db:
            db.execute('INSERT INTO questions VALUES (?, ?, ?, ?)',
                       (answer['id'], answer['reading_id'], answer['created_at'], json.dumps(answer, ensure_ascii=False)))

    def question_history(self, reading_id):
        with self.connect() as db:
            rows = db.execute('SELECT payload FROM questions WHERE reading_id=? ORDER BY created_at DESC LIMIT 50', (reading_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def save_comparison(self, answer):
        with self.connect() as db:
            db.execute('INSERT INTO comparisons VALUES (?, ?, ?)',
                (answer['id'], answer['created_at'], json.dumps(answer, ensure_ascii=False)))

    def comparison_history(self):
        with self.connect() as db:
            rows = db.execute('SELECT payload FROM comparisons ORDER BY created_at DESC LIMIT 20').fetchall()
        return [json.loads(row[0]) for row in rows]

    def save_agent_run(self, run):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO agent_runs VALUES (?, ?, ?)',
                (run['id'], run['created_at'], json.dumps(run, ensure_ascii=False)))

    def agent_run(self, identifier):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM agent_runs WHERE id=?', (identifier,)).fetchone()
        return json.loads(row[0]) if row else None

    def agent_history(self):
        with self.connect() as db:
            rows = db.execute('SELECT payload FROM agent_runs ORDER BY created_at DESC LIMIT 20').fetchall()
        return [json.loads(row[0]) for row in rows]

    def interrupt_agent_runs(self):
        with self.connect() as db:
            rows = db.execute('SELECT payload FROM agent_runs').fetchall()
            for (payload,) in rows:
                run = json.loads(payload)
                if run['status'] in ('queued', 'running'):
                    run.update(status='interrupted', message='服务已重启，任务已中断；已有结果保留，可重新发起任务。')
                    db.execute('UPDATE agent_runs SET payload=? WHERE id=?', (json.dumps(run, ensure_ascii=False), run['id']))
