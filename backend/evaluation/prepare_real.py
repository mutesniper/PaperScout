"""使用本地已提取论文构建真实论文数据集，不修改论文库。"""
import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from evaluation.run import Dataset


def text_hash(doc):
    material = {'page_count': doc['page_count'], 'sections': doc['sections']}
    return hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def prepare(database, manifest_path, output):
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    # 使用只读连接，不创建或更新应用数据库。
    with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True) as db:
        available = {}
        for (payload,) in db.execute('SELECT payload FROM readings'):
            doc = json.loads(payload)
            available[text_hash(doc)] = doc
    documents = []
    for paper in manifest['papers']:
        doc = available.get(paper['text_sha256'])
        if doc is None:
            raise ValueError(f"缺少对应版本的提取文本：{paper['title']}；不得静默替换版本。")
        documents.append({**doc, 'id': paper['id'], 'title': paper['title'],
            'source_url': paper['source_url'], 'created_at': '2026-10-25',
            'summaries': [], 'document_summary': None})
    data = Dataset(version=manifest['version'], description=manifest['description'],
                   documents=documents, cases=manifest['cases'])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(data.model_dump_json(indent=2), encoding='utf-8')
    print(f'已生成 {len(documents)} 篇完整提取文本、{len(data.cases)} 道题：{output}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=ROOT / 'backend/data/paperscout.db')
    parser.add_argument('--manifest', type=Path, default=Path(__file__).parent / 'real_manifest.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/evaluation/real-papers.json')
    args = parser.parse_args()
    prepare(args.database, args.manifest, args.output)
