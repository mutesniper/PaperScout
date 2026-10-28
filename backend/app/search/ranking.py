"""Local candidate ranking. No inferred dates, citations or semantic scores."""
from app.search.providers import deduplicate

def source_candidates(candidates, source):
    papers = deduplicate(candidates)
    for rank, paper in enumerate(papers, start=1):
        paper.source_ranks = {source: rank}
    return papers

def rank_candidates(papers, mode):
    for paper in papers:
        paper.relevance_score = sum(1 / (60 + rank) for rank in paper.source_ranks.values())
    # Stable ties retain first-seen order; a single source keeps its original order.
    relevant = sorted(papers, key=lambda p: p.relevance_score, reverse=True)
    if mode == 'relevance':
        return relevant
    if mode == 'citations':
        return sorted(relevant, key=lambda p: (p.citation_count is None, -(p.citation_count or 0)))
    direction = -1 if mode == 'newest' else 1
    def date_key(p):
        year = p.publication_date.year if p.publication_date else p.year
        # Year-only entries follow exact dates in the same year, in either direction.
        return (year is None, direction * (year or 0), p.publication_date is None,
                direction * (p.publication_date.toordinal() if p.publication_date else 0))
    return sorted(relevant, key=date_key)

def ranking_details(papers, mode):
    notes = {
        'relevance': '每行独立查询，同来源各组结果交替合并去重，最多保留 100 篇；多来源使用 RRF（k=60）融合。',
        'newest': '按本次候选论文的发表日期从新到旧排序，不代表全库最新。arXiv 使用首次提交日期。',
        'oldest': '按本次候选论文的发表日期从旧到新排序，不代表全库最早。arXiv 使用首次提交日期。',
        'citations': '按本次候选论文的 Semantic Scholar 引用次数排序，不代表全库引用最高。',
    }
    warnings = []
    if mode == 'citations':
        missing = sum(p.citation_count is None for p in papers)
        if missing:
            warnings.append(f'{missing}/{len(papers)} 篇候选论文引用次数未知，放在已知数据之后；arXiv 不提供引用次数。')
    if mode in ('newest', 'oldest'):
        warnings.append('只有年份时按年份分组，排在同年精确日期之后；日期和年份均未知时放最后。')
    return notes[mode], warnings
