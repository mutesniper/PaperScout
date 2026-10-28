"""Local route decisions with explicit identity, resource and budget validation."""
import hashlib
import httpx
from pydantic import Field
from app.agent.router_contract import State, SYSTEM_PROMPT, parse_decision
from app.tools.schemas import Strict

ADAPTER_SHA256 = '0eb66e6ea52df5075a59d65749e9cc6da8a43872247f2ebaa6354d563f1db458'
PROMPT_SHA256 = hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()


class RouterError(RuntimeError):
    pass


class Clarification(Strict):
    question: str = Field(min_length=2, max_length=500)


class Completion(Strict):
    reason: str = Field(min_length=2, max_length=200)
    completed: bool


class LocalRouter:
    def __init__(self, url):
        self.url = url.rstrip('/')

    async def decide(self, state):
        try:
            # Local traffic must not accidentally pass through system proxies.
            async with httpx.AsyncClient(timeout=45, trust_env=False) as client:
                response = await client.post(self.url+'/route', json=state.model_dump())
                if response.status_code in (413,422):
                    raise RouterError('任务状态超出本地路由范围（最多2048 Token），请缩小任务或改用云端模式。')
                response.raise_for_status()
                data = response.json()
            if data.get('adapter_sha256') != ADAPTER_SHA256 or data.get('prompt_sha256') != PROMPT_SHA256:
                raise RouterError('本地路由服务的模型或提示版本不匹配，请使用最终版适配器启动。')
            return parse_decision(data['raw'], state).route
        except RouterError:
            raise
        except httpx.HTTPError:
            raise RouterError('本地路由服务不可用、繁忙或请求超时，请启动或检查 WSL 小模型服务；未切换到云端路由。') from None
        except (ValueError, KeyError, TypeError, AttributeError):
            raise RouterError('小模型返回了无效或资源、预算不允许的操作，已停止执行。') from None


def routing_state(run, candidates, outcomes, counts, limits, steps):
    results = list(run.get('user_replies', []))
    for answer in run['answers']:
        results.append(f"已完成单篇问答：{answer.get('reading_id', '')}；问题：{answer['question']}。"
                       + ('有证据支持。' if answer.get('supported') else '本次回答未找到足够依据。'))
    comparison = run.get('comparison')
    if comparison:
        results.append('已生成论文对比：'+', '.join(p['title'] for p in comparison['papers'])+
                       '；维度：'+', '.join(comparison['dimensions'])+'。')
    if candidates:
        results.append('已检索到候选并提供来源链接：'+', '.join(p['title'] for p in candidates.values()))
    recent = list(run.get('tool_outcomes', []))
    # Keep every download attempt; never drop failures when constructing model input.
    recent += [{'operation':'download_pdf','target_id':key,
                'status':'success' if value['status']=='ok' else 'failed',
                'detail':value.get('reason') or ('已导入PDF：'+value['title'])}
               for key,value in outcomes.items()]
    try:
        return State(goal=run['goal'], candidates=[{k:p[k] for k in ('paper_id','title','public_pdf')}
                    for p in candidates.values()],
            available_pdfs=[{'reading_id':p['id'],'paper_id':p.get('paper_id'),'title':p['title']}
                            for p in run['readings']], results=results, recent_outcomes=recent,
            remaining={**{k:limits[k]-counts[k] for k in limits},'steps':steps}, cancelled=False)
    except ValueError:
        raise RouterError('当前任务状态超出本地路由输入范围，请缩短目标或使用云端模式。') from None
