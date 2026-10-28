import asyncio
import re
import time
from datetime import date
from urllib.parse import urlparse
import httpx
from defusedxml import ElementTree
from app.schemas import Paper, Plan

def safe_url(value):
    if isinstance(value, str) and urlparse(value).scheme in ('http', 'https') and urlparse(value).netloc:
        return value
    return None

def doi_key(value):
    return re.sub(r'^(https?://(dx\.)?doi\.org/|doi:\s*)', '', value.strip(), flags=re.I).lower() if value else None

def arxiv_key(value):
    return re.sub(r'v\d+$', '', value.split('/abs/')[-1]) if value else None


def with_preprint_link(paper):
    """根据已校验的 arXiv 标识补全 PDF 元数据，不通过标题猜测。"""
    if paper.fulltext_url:
        return paper
    identifier = paper.arxiv_id
    if not identifier and paper.doi and paper.doi.lower().startswith('10.48550/arxiv.'):
        identifier = paper.doi[len('10.48550/arxiv.'):]
    if not identifier or not re.fullmatch(r'(?:\d{4}\.\d{4,5}|[a-zA-Z][a-zA-Z.-]*/\d{7})(?:v\d+)?', identifier):
        return paper
    return paper.model_copy(update={
        'arxiv_id': arxiv_key(identifier),
        'fulltext_url': 'https://arxiv.org/pdf/' + identifier,
        'access': 'open_preprint',
        'evidence': '根据来源中的 arXiv 标识补全预印本 PDF 链接；尚未验证下载。',
    })

def parse_date(value):
    try:
        return date.fromisoformat(value[:10]) if value else None
    except (ValueError, TypeError):
        return None

def parse_arxiv(content):
    try:
        root = ElementTree.fromstring(content)
    except Exception as exc:
        raise ValueError('arXiv 返回的 XML 无法解析') from exc
    ns = {'a': 'http://www.w3.org/2005/Atom', 'x': 'http://arxiv.org/schemas/atom'}
    papers = []
    for entry in root.findall('a:entry', ns):
        identifier = entry.findtext('a:id', '', ns)
        if '/api/errors' in identifier:
            raise ValueError('arXiv 拒绝了检索条件')
        title = ' '.join(entry.findtext('a:title', '', ns).split())
        if not title or not identifier:
            continue
        aid = arxiv_key(identifier)
        published = entry.findtext('a:published', '', ns)
        pdf = next((safe_url(link.get('href')) for link in entry.findall('a:link', ns) if link.get('title') == 'pdf'), None)
        papers.append(Paper(id='arxiv:' + aid, title=title,
            authors=[a.findtext('a:name', '', ns) for a in entry.findall('a:author', ns)],
            year=int(published[:4]) if published[:4].isdigit() else None,
            publication_date=parse_date(published),
            abstract=' '.join(entry.findtext('a:summary', '', ns).split()),
            doi=doi_key(entry.findtext('x:doi', None, ns)), arxiv_id=aid,
            sources=['arxiv'], url=safe_url(identifier) or 'https://arxiv.org',
            fulltext_url=pdf, access='open_preprint' if pdf else 'abstract_only',
            evidence='arXiv 元数据提供预印本 PDF 链接；尚未验证下载。' if pdf else '已获取 arXiv 摘要，未取得 PDF 链接。'))
    return [with_preprint_link(paper) for paper in papers]

def parse_semantic(payload):
    papers = []
    for item in payload['data']:
        if not item.get('title') or not item.get('paperId'):
            continue
        ids = item.get('externalIds') or {}
        pdf = safe_url((item.get('openAccessPdf') or {}).get('url'))
        aid = arxiv_key(ids.get('ArXiv'))
        abstract = item.get('abstract') or ''
        preprint = bool(pdf and (urlparse(pdf).hostname or '').lower() in ('arxiv.org', 'export.arxiv.org'))
        access = ('open_preprint' if preprint else 'open_fulltext') if pdf else ('abstract_only' if abstract else 'unknown')
        papers.append(Paper(id='s2:' + item['paperId'], title=item['title'],
            authors=[a['name'] for a in item.get('authors', []) if a.get('name')],
            year=item.get('year'), publication_date=parse_date(item.get('publicationDate')),
            citation_count=item.get('citationCount'),
            citation_source='semantic_scholar' if item.get('citationCount') is not None else None,
            abstract=abstract, doi=doi_key(ids.get('DOI')), arxiv_id=aid,
            sources=['semantic_scholar'], url=safe_url(item.get('url')) or 'https://www.semanticscholar.org/paper/' + item['paperId'],
            fulltext_url=pdf, access=access,
            evidence='Semantic Scholar 元数据提供公开全文链接；尚未验证下载。' if pdf else '来源未提供公开全文链接，无法据此判断是否付费。'))
    return [with_preprint_link(paper) for paper in papers]

def deduplicate(papers):
    output = []
    def same(a, b):
        if a.doi and b.doi:
            return a.doi == b.doi
        if a.arxiv_id and b.arxiv_id:
            return a.arxiv_id == b.arxiv_id
        if a.id == b.id:
            return True
        title_a = ' '.join(a.title.casefold().split())
        title_b = ' '.join(b.title.casefold().split())
        return (len(title_a) >= 25 and title_a == title_b and a.year is not None and a.year == b.year
                and bool(a.authors and b.authors) and a.authors[0].casefold() == b.authors[0].casefold())
    for paper in papers:
        found = next((p for p in output if same(p, paper)), None)
        if found is None:
            output.append(paper.model_copy(deep=True))
            continue
        found.sources = list(dict.fromkeys(found.sources + paper.sources))
        found.doi = found.doi or paper.doi
        found.arxiv_id = found.arxiv_id or paper.arxiv_id
        found.year = found.year or paper.year
        # 保留首个来源的日期；arXiv 使用首次提交日期，而非修订日期。
        found.publication_date = found.publication_date or paper.publication_date
        if found.citation_count is None and paper.citation_count is not None:
            found.citation_count, found.citation_source = paper.citation_count, paper.citation_source
        for source, rank in paper.source_ranks.items():
            found.source_ranks[source] = min(found.source_ranks.get(source, rank), rank)
        if len(paper.abstract) > len(found.abstract):
            found.abstract = paper.abstract
        if paper.fulltext_url and not found.fulltext_url:
            found.fulltext_url, found.access, found.evidence = paper.fulltext_url, paper.access, paper.evidence
    return output

class Sources:
    def __init__(self, client, semantic_key=''):
        self.client = client
        self.semantic_key = semantic_key
        self.locks = {s: asyncio.Lock() for s in ('arxiv', 'semantic_scholar')}
        self.last = {s: 0.0 for s in self.locks}

    async def fetch(self, source, query, plan: Plan, limit):
        # 候选策略：来源支持时按相关性查询，否则采用
        # 来源的默认顺序，不替换为用户选择的本地排序方式。
        # 默认排序列表可以参与 RRF 融合，但不代表语义得分。
        if source == 'arxiv':
            terms = re.findall(r'[\w-]+', query, re.UNICODE)
            if not terms:
                return []
            expression = ' AND '.join('all:' + term for term in terms)
            if plan.year_from or plan.year_to:
                expression += f' AND submittedDate:[{plan.year_from or 1900}01010000 TO {plan.year_to or 2100}12312359]'
            url = 'https://export.arxiv.org/api/query'
            params = dict(search_query=expression, max_results=limit, sortBy='relevance')
            headers = {}
        else:
            # /paper/search 本身按相关性搜索，无需排序参数；
            # 不要改用批量接口默认的 ID 顺序。
            url = 'https://api.semanticscholar.org/graph/v1/paper/search'
            params = dict(query=query, limit=limit, fields='title,authors,year,publicationDate,citationCount,abstract,url,externalIds,openAccessPdf')
            if plan.year_from or plan.year_to:
                params['year'] = f'{plan.year_from or ""}-{plan.year_to or ""}'
            headers = {'x-api-key': self.semantic_key} if self.semantic_key else {}
        for attempt in range(3):
            try:
                async with self.locks[source]:
                    interval = 3.1 if source == 'arxiv' else 1.1
                    await asyncio.sleep(max(0, interval - (time.monotonic() - self.last[source])))
                    self.last[source] = time.monotonic()
                    response = await self.client.get(url, params=params, headers=headers)
                response.raise_for_status()
                return parse_arxiv(response.content) if source == 'arxiv' else parse_semantic(response.json())
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in (429, 500, 502, 503, 504) or attempt == 2:
                    raise
                retry = exc.response.headers.get('Retry-After', '')
                delay = min(float(retry), 8) if retry.isdigit() else 2 ** (attempt + 1)
                await asyncio.sleep(delay)
            except httpx.TransportError:
                if attempt == 2:
                    raise
                await asyncio.sleep(2 ** attempt)
