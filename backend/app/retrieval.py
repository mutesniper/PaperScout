"""Single-document retrieval; vector caches are keyed by content and provider."""
import hashlib
import json
import math
import re
from collections import Counter
from typing import Protocol
import numpy as np
import faiss


class EmbeddingProvider(Protocol):
    configured: bool
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


def vectors_array(values, count):
    values = np.asarray(values, dtype='float32')
    if values.ndim != 2 or values.shape[0] != count or not 0 < values.shape[1] <= 8192:
        raise ValueError('invalid embedding dimensions')
    if not np.isfinite(values).all() or (np.linalg.norm(values, axis=1) == 0).any():
        raise ValueError('invalid embedding values')
    return np.ascontiguousarray(values)


class CloudEmbedding:
    def __init__(self, client, base_url, api_key, name):
        self.client, self.base_url, self.api_key, self.name = client, base_url.rstrip('/'), api_key, name

    @property
    def configured(self):
        return bool(self.base_url and self.api_key and self.name)

    @property
    def partial(self):
        return bool(self.base_url or self.api_key or self.name) and not self.configured

    async def embed(self, texts):
        response = await self.client.post(self.base_url + '/embeddings', timeout=45,
            headers={'Authorization': 'Bearer ' + self.api_key},
            json={'model': self.name, 'input': texts, 'encoding_format': 'float'})
        response.raise_for_status()
        rows = response.json()['data']
        if len(rows) != len(texts) or sorted(row['index'] for row in rows) != list(range(len(texts))):
            raise ValueError('invalid embedding response indices')
        vectors = [row['embedding'] for row in sorted(rows, key=lambda row: row['index'])]
        return vectors_array(vectors, len(texts)).tolist()


def chunks_for(doc):
    chunks = []
    for section in doc.sections:
        for fragment in section.fragments:
            text = ' '.join(fragment.text.split())
            # Overlap preserves local context at boundaries. Never cross pages.
            for start in range(0, len(text), 850):
                part = text[start:start + 1000]
                if len(part) >= 10:
                    chunks.append({'id': f'c{len(chunks) + 1}', 'section_id': section.id,
                        'section': section.title, 'page': fragment.page, 'text': part})
                if start + 1000 >= len(text):
                    break
    return chunks


STOP = set('the a an is are was were this that what how which of to in on for and or does do paper study'.split())


def tokens(text):
    english = [x for x in re.findall(r'[a-z0-9]+', text.lower()) if x not in STOP]
    chinese = re.findall(r'[\u3400-\u9fff]+', text)
    return english + [part[i:i + 2] for part in chinese for i in range(max(1, len(part) - 1))]


def lexical_scores(chunks, question):
    # Small explicit bilingual hints keep common Chinese questions usable without
    # an embedding service. This is not general cross-language semantic search.
    hints = {'方法': 'method approach framework', '实验': 'experiment evaluation',
        '结果': 'results performance', '局限': 'limitation limitations', '贡献': 'contribution contributions',
        '数据集': 'dataset benchmark', '指标': 'metric accuracy precision', '训练': 'training', '推理': 'inference'}
    question += ' ' + ' '.join(value for key, value in hints.items() if key in question)
    query = set(tokens(question))
    counts = [Counter(tokens(c['section'] + ' ' + c['text'])) for c in chunks]
    average = sum(sum(c.values()) for c in counts) / max(1, len(counts))
    df = {term: sum(term in count for count in counts) for term in query}
    result = []
    for count in counts:
        length = sum(count.values())
        score = sum(math.log(1 + (len(counts) - df[t] + .5) / (df[t] + .5)) *
                    (count[t] * 2.2 / (count[t] + 1.2 * (.25 + .75 * length / max(average, 1))))
                    for t in query if count[t])
        result.append(score)
    return result


async def retrieve(doc, question, embedding, store, lock):
    chunks = chunks_for(doc)
    if not chunks:
        return [], 'keyword'
    scores = lexical_scores(chunks, question)
    if not embedding.configured:
        order = sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)
        return [chunks[i] for i in order if scores[i] > 0][:6], 'keyword'
    key = hashlib.sha256(json.dumps(['qa-v1', doc.id, embedding.base_url, embedding.name, chunks],
        ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    async with lock:
        cached = store.vector_cache(key)
        if cached is None:
            cached = []
            for start in range(0, len(chunks), 8):
                cached.extend(await embedding.embed([c['text'] for c in chunks[start:start + 8]]))
            vectors_array(cached, len(chunks))
            store.save_vector_cache(key, cached)
    matrix = vectors_array(cached, len(chunks))
    query = vectors_array(await embedding.embed([question]), 1)
    if query.shape[1] != matrix.shape[1]:
        raise ValueError('embedding dimension changed')
    faiss.normalize_L2(matrix)
    faiss.normalize_L2(query)
    index = faiss.IndexFlatIP(matrix.shape[1])
    index.add(matrix)
    distances, matches = index.search(query, min(12, len(chunks)))
    # Mix lexical and semantic rankings without treating distance as proof.
    ranks = {int(i): 1 / (60 + rank) for rank, (i, score) in
             enumerate(zip(matches[0], distances[0]), 1) if i >= 0 and score > 0}
    for rank, i in enumerate(sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True), 1):
        if scores[i] > 0:
            ranks[i] = ranks.get(i, 0) + 1 / (60 + rank)
    return [chunks[i] for i in sorted(ranks, key=ranks.get, reverse=True)[:6]], 'semantic'
