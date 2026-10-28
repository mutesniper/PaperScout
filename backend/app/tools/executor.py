"""Task-scoped tool execution, evidence services and result bookkeeping."""
import httpx
from fastapi import HTTPException
from app.papers.schemas import ImportPaper
from app.papers.service import import_paper
from app.rag.comparison import compare
from app.rag.qa import Question, ask
from app.tools.search import search_arxiv_first

class JobRequest:
    """Internal service context; job cancellation is owned by the task runner."""
    def __init__(self, app):
        self.app = app

    async def is_disconnected(self):
        return False


class ToolExecutor:
    def __init__(self, app, run, event):
        self.app = app
        self.run = run
        self.event = event
        self.request = JobRequest(app)
        saved = run.get('router_checkpoint', {})
        self.candidates = dict(saved.get('candidates', {}))
        self.read_outcomes = dict(saved.get('read_outcomes', {}))

    async def execute(self, action):
        app, run, event = self.app, self.run, self.event
        store, request = app.state.store, self.request
        candidates, read_outcomes = self.candidates, self.read_outcomes
        tool = action.tool
        label = candidates.get(action.paper_id, {}).get('title', action.paper_id) if tool == 'download_pdf' else ''
        event(tool, 'running', (label + '：' if label else '') + action.reason)
        try:
            if tool == 'search':
                result = await search_arxiv_first(app, action.plan, event)
                store.save(result)
                run['search_ids'].append(result.id)
                for paper in result.papers:
                    candidates[paper.id] = {'paper_id': paper.id, 'record_id': result.id,
                        'title': paper.title, 'abstract': paper.abstract[:600], 'url': paper.url,
                        'public_pdf': bool(paper.fulltext_url and paper.access in ('open_fulltext', 'open_preprint'))}
                    for ready in run['readings']:
                        if ready.get('paper_id') == paper.id or ready.get('source_url') and ready['source_url'] in (paper.url,paper.fulltext_url):
                            ready['paper_id'] = paper.id
                            read_outcomes[paper.id] = {'status':'ok','reading_id':ready['id'],'title':ready['title']}
                run['papers'] = list(candidates.values())[:20]
                failures = [source.source + '：' + source.message for source in result.sources if source.status != 'ok']
                empty = [source.source + '：检索成功但无匹配论文' for source in result.sources
                         if source.status == 'ok' and source.count == 0]
                event(tool, 'ok' if not failures else 'partial',
                    f'获取 {len(result.papers)} 篇候选。' + '；'.join(empty + failures))
            elif tool == 'download_pdf':
                paper = candidates.get(action.paper_id)
                if not paper or not paper['public_pdf']:
                    raise HTTPException(422, '该论文不是本任务可获取公开 PDF 的候选；可手动上传后重新发起。')
                doc = await import_paper(ImportPaper(record_id=paper['record_id'], paper_id=action.paper_id, mode='pdf'), request)
                if doc.id not in {d['id'] for d in run['readings']}:
                    run['readings'].append({'id': doc.id, 'title': doc.title, 'paper_id': action.paper_id})
                read_outcomes[action.paper_id] = {'status': 'ok', 'reading_id': doc.id, 'title': doc.title}
                event(tool, 'ok', '已获取并解析：' + doc.title)
            elif tool == 'compare':
                allowed = {doc['id'] for doc in run['readings']}
                if not set(action.args.reading_ids) <= allowed:
                    raise HTTPException(422, '只能对比本任务已获取或用户选定的 PDF。')
                run['comparison'] = await compare(action.args, request)
                event(tool, 'ok', '已生成带原文证据的对比结果。')
            elif tool == 'answer':
                if action.reading_id not in {doc['id'] for doc in run['readings']}:
                    raise HTTPException(422, '只能阅读本任务已获取或用户选定的 PDF。')
                run['answers'].append(await ask(action.reading_id, Question(question=action.question), request))
                event(tool, 'ok', '已完成单篇证据问答。')
        except HTTPException as exc:
            if tool == 'download_pdf':
                read_outcomes[action.paper_id] = {'status': 'failed', 'title': label, 'reason': str(exc.detail)}
            event(tool, 'error', (label + '：' if label else '') + str(exc.detail))
        except (httpx.HTTPError, ValueError, KeyError, TypeError, OSError):
            if tool == 'download_pdf':
                read_outcomes[action.paper_id] = {'status': 'failed', 'title': label, 'reason': '工具执行失败'}
            event(tool, 'error', '工具执行失败，未获得有效结果；可换用其他候选或结束。')


