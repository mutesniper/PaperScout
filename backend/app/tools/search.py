"""Agent search policy; manual search remains in the HTTP service."""
import re
from ..schemas import SearchRequest
from ..providers import deduplicate
from ..ranking import rank_candidates

ARXIV_CANDIDATE_TARGET = 5


async def search_arxiv_first(app, plan, event):
    # One focused query per action; the agent can revise it on its second search.
    focused = plan.model_copy(update={'queries': plan.queries[:1]})
    event('search', 'running', '优先搜索 arXiv，使用一组核心检索词。')
    primary = await app.state.collect_search(SearchRequest(plan=focused, sources=['arxiv'], limit=100))
    # Full titles may include unindexed function words. Retry only a successful
    # empty response, not rate limits/network errors, with a bounded shorter query.
    terms = re.findall(r'[\w-]+', focused.queries[0], re.UNICODE)
    core = [term for term in terms if term.lower() not in {'a', 'an', 'the', 'at', 'of', 'in', 'on', 'for', 'to', 'and'}]
    if (not primary.papers and primary.sources and all(s.status == 'ok' for s in primary.sources)
            and len(core) >= 2 and len(core) < len(terms)):
        simplified = focused.model_copy(update={'queries': [' '.join(core)]})
        event('search', 'running', 'arXiv 返回空结果，去掉常见虚词后重试：' + simplified.queries[0])
        primary = await app.state.collect_search(SearchRequest(plan=simplified, sources=['arxiv'], limit=100))
    pool = deduplicate(primary.papers)
    statuses = primary.sources
    sources = ['arxiv']
    warnings = list(primary.warnings)
    public_count = sum(bool(p.fulltext_url and p.access in ('open_fulltext', 'open_preprint')) for p in pool)
    def title_key(text):
        return re.findall(r'[\w-]+', text.casefold(), re.UNICODE)
    exact_public = any(title_key(p.title) == title_key(focused.queries[0]) and p.fulltext_url
                       and p.access in ('open_fulltext', 'open_preprint') for p in pool)
    if (public_count < ARXIV_CANDIDATE_TARGET and not exact_public) or any(s.status != 'ok' for s in statuses):
        event('search', 'running', f'arXiv 提供 {public_count} 篇带公开全文链接的候选，或来源请求未完整成功；补充搜索 Semantic Scholar。')
        secondary = await app.state.collect_search(SearchRequest(plan=focused, sources=['semantic_scholar'], limit=100))
        pool = deduplicate(pool + secondary.papers)
        statuses = statuses + secondary.sources
        sources.append('semantic_scholar')
        warnings.extend(secondary.warnings)
    else:
        event('search', 'ok', ('arXiv 已找到标题匹配且带公开全文链接的论文' if exact_public else
              f'arXiv 已提供 {public_count} 篇带公开全文链接的候选') + '，跳过 Semantic Scholar。')
    return primary.model_copy(update={
        'request': SearchRequest(plan=focused, sources=sources, limit=10),
        'papers': rank_candidates(pool, 'relevance')[:10],
        'sources': statuses, 'candidate_count': len(pool), 'warnings': list(dict.fromkeys(warnings)),
        'ranking_note': 'Agent 优先 arXiv；空结果时最多简化虚词重试一次。标题匹配且有公开全文时跳过补充来源，否则公开全文候选不足 5 篇或来源失败时补充 Semantic Scholar。按 RRF 返回前 10 篇。',
    })


