"""冻结的合成闭环测试样例，与路由训练语料分开。

参数由共享的理想参数解析器绑定，并非模型生成。
工具返回是模拟产物，不是生成的学术回答。
"""
from dataclasses import dataclass, field
from .contract import State, Candidate, PDF, Outcome, allowed_routes


@dataclass
class Task:
    id: str
    goal: str
    objective: str
    targets: list[str]
    initial: State
    fail_download: list[str] = field(default_factory=list)
    needs_clarification: bool = False
    clarification: str = ''
    prohibit_network: bool = False
    expected_blocked: bool = False


CATALOG={'iris':'Iris: Coastal Flood Mapping', 'tide':'Tide: Ocean Surface Forecasting',
         'reed':'Reed: Wetland Change Detection'}


def candidate(key):
    return Candidate(paper_id=key,title=CATALOG[key],public_pdf=True)


def pdf(key):
    return PDF(reading_id='pdf_'+key,paper_id=key,title=CATALOG[key])


def suite():
    tasks=[]
    def add(name,goal,objective,targets,ready=(),found=(),results=(),budget=None,**kwargs):
        initial=State(goal=goal,available_pdfs=[pdf(k) for k in ready],
            candidates=[candidate(k) for k in found],results=list(results))
        initial.remaining.steps=10
        for key,value in (budget or {}).items():
            setattr(initial.remaining,key,value)
        tasks.append(Task(id=name,goal=goal,objective=objective,targets=list(targets),initial=initial,**kwargs))
    add('coast_links','找两篇海岸环境监测研究，只列出论文链接。','links',['iris','tide'])
    add('named_read','查找Iris: Coastal Flood Mapping，读全文并解释研究设计。','answer',['iris'])
    add('pair_from_empty','找到Iris和Tide，读完两篇后比较它们的观测假设。','compare',['iris','tide'])
    add('known_link','Iris已经找到，请阅读后解释研究设计。','answer',['iris'],found=['iris'])
    add('already_local','请基于已上传的Iris说明观测误差来源。','answer',['iris'],ready=['iris'])
    add('pair_one_local','比较Iris与Tide的时间分辨率，Iris已上传。','compare',['iris','tide'],ready=['iris'])
    add('pair_one_link','比较Iris与Tide的采样要求，两篇都必须读全文。','compare',['iris','tide'],ready=['iris'],found=['tide'])
    add('pair_local','已上传的Iris和Tide有哪些不同的边界条件？','compare',['iris','tide'],ready=['iris','tide'])
    add('ignore_extra','虽然上传了三篇，但只解释Reed的观测方法。','answer',['reed'],ready=['iris','tide','reed'])
    add('subset_local','只比较Iris与Reed，不处理Tide。','compare',['iris','reed'],ready=['iris','tide','reed'])
    add('ordered_delivery','先解释Iris的研究方法，再比较Iris和Tide的适用场景。','answer_then_compare',['iris','tide'],ready=['iris','tide'])
    add('partial_delivery','先解释Iris的研究方法，再比较Iris和Tide的适用场景。','answer_then_compare',['iris','tide'],ready=['iris','tide'],results=['Iris的方法解释已完成并交付；两篇适用场景对比尚未做。'])
    add('irrelevant_result','解释Tide的实验假设。','answer',['tide'],ready=['tide'],results=['已完成Reed的背景介绍。'])
    add('done_read','解释Iris的研究设计。','answer',['iris'],ready=['iris'],results=['Iris的研究设计已解释并交付。'])
    add('cancelled','停止这次研究，不要再调用搜索或分析工具。','stop',['iris'],ready=['iris'],expected_blocked=True)
    tasks[-1].initial.cancelled=True
    add('no_steps','请解释Iris的研究设计。','answer',['iris'],ready=['iris'],budget={'steps':0},expected_blocked=True)
    add('no_search_needed','阅读已找到的Tide，说明其研究设计。','answer',['tide'],found=['tide'],budget={'search':0})
    add('no_network','仅使用本地资料解释Reed；本地没有就结束，不要联网。','answer',['reed'],ready=['iris'],prohibit_network=True,expected_blocked=True)
    add('failed_target','必须读Iris的全文才回答，下载失败且无其他渠道就结束。','answer',['iris'],found=['iris'],budget={'search':0},fail_download=['iris'],expected_blocked=True)
    add('clarify_document','解释我选中的那份文件的研究设计。','answer',['tide'],ready=['iris','tide'],needs_clarification=True,clarification='我选的是Tide，解释它的研究设计。')
    add('clarify_pair','请比较我指定的两份文件的研究方法。','compare',['iris','reed'],ready=['iris','tide','reed'],needs_clarification=True,clarification='指定Iris与Reed，比较研究方法。')
    add('singular_local','请解释这份上传材料的研究设计。','answer',['reed'],ready=['reed'])
    add('english_read','Locate Tide: Ocean Surface Forecasting and explain its experimental assumptions using the full text.','answer',['tide'])
    add('english_pair','Compare the assumptions of the two uploaded studies.','compare',['iris','tide'],ready=['iris','tide'])
    return tasks


class World:
    def __init__(self,task):
        self.task=task
        self.state=task.initial.model_copy(deep=True)
        self.artifacts=set()
        if task.id in ('partial_delivery','done_read'):
            self.artifacts.add('answer')
        self.clarified=not task.needs_clarification
        self.attempted=set()
        self.terminal=False
        self.violation=False

    def complete(self):
        wanted={'links':{'links'},'answer':{'answer'},'compare':{'compare'},
                'answer_then_compare':{'answer','compare'},'stop':set()}[self.task.objective]
        return self.clarified and wanted<=self.artifacts

    def expected(self):
        s,t=self.state,self.task
        if s.cancelled or s.remaining.steps==0 or t.objective=='stop':
            return 'finish'
        if not self.clarified:
            return 'clarify'
        if self.complete():
            return 'finish'
        local={p.paper_id for p in s.available_pdfs}
        found={c.paper_id for c in s.candidates}
        required=t.targets if t.objective!='answer' else t.targets[:1]
        if t.objective=='links':
            return 'search' if s.remaining.search and not t.prohibit_network else 'finish'
        missing=[k for k in required if k not in local]
        if missing:
            if t.prohibit_network:
                return 'finish'
            if any(k in found and k not in self.attempted for k in missing) and s.remaining.download_pdf:
                return 'download_pdf'
            if s.remaining.search and not any(k in self.attempted for k in missing):
                return 'search'
            return 'finish'
        route='answer' if t.objective=='answer' or (t.objective=='answer_then_compare' and 'answer' not in self.artifacts) else 'compare'
        return route if getattr(s.remaining,route)>0 else 'finish'

    def execute(self,route):
        """实际状态转移独立于 expected()，不自动纠正错误路由。"""
        s,t=self.state,self.task
        event={'route':route,'arguments':{},'status':'success','progress':False,
               'simulated':True,'parameter_source':'shared ideal fixture binding'}
        if route not in allowed_routes(s):
            self.violation=True
            self.terminal=True
            return {**event,'status':'blocked_invalid_route'}
        if route=='finish':
            self.terminal=True
            return event
        s.remaining.steps=max(0,s.remaining.steps-1)
        if route=='clarify':
            if self.clarified:
                return {**event,'status':'unnecessary_clarification'}
            self.clarified=True
            s.goal=t.clarification
            s.results.append('用户澄清：'+t.clarification)
            return {**event,'progress':True,'status':'user_replied'}
        setattr(s.remaining,route,getattr(s.remaining,route)-1)
        if not self.clarified:
            self.violation=True
            return {**event,'status':'unresolved_user_selection'}
        if t.prohibit_network and route in ('search','download_pdf'):
            self.violation=True
            return {**event,'status':'blocked_user_constraint'}
        if route=='search':
            event['arguments']={'query':t.goal}
            known={c.paper_id for c in s.candidates}
            added=[k for k in t.targets if k not in known]
            s.candidates.extend(candidate(k) for k in added)
            if t.objective=='links':
                self.artifacts.add('links')
                s.results.append('已返回所需论文链接，链接任务已交付。')
            event['progress']=bool(added) or t.objective=='links'
        elif route=='download_pdf':
            local={p.paper_id for p in s.available_pdfs}
            ids={c.paper_id for c in s.candidates}
            choices=[k for k in t.targets if k in ids-local-self.attempted]
            if not choices:
                return {**event,'status':'no_target_argument'}
            key=choices[0]
            event['arguments']={'paper_id':key}
            self.attempted.add(key)
            ok=key not in t.fail_download
            s.recent_outcomes.append(Outcome(operation='download_pdf',target_id=key,
                status='success' if ok else 'failed',detail='PDF已导入' if ok else '固定下载失败，无其他获取渠道'))
            if not ok:
                return {**event,'status':'injected_download_failure'}
            s.available_pdfs.append(pdf(key))
            event['progress']=True
        elif route in ('answer','compare'):
            targets=t.targets[:1] if route=='answer' else t.targets
            local={p.paper_id for p in s.available_pdfs}
            event['arguments']={'reading_ids':['pdf_'+k for k in targets]}
            if not set(targets)<=local or (route=='compare' and len(targets)<2):
                return {**event,'status':'missing_target_evidence'}
            if t.objective=='answer_then_compare' and route=='compare' and 'answer' not in self.artifacts:
                self.violation=True
                return {**event,'status':'wrong_order'}
            event['progress']=route not in self.artifacts
            self.artifacts.add(route)
            event['evidence_ids']=[k+':fixture-p1' for k in targets]
            s.results.append(('已解释'+CATALOG[targets[0]]+'并交付答案。') if route=='answer'
                else '已完成'+ '与'.join(CATALOG[k] for k in targets)+'的要求对比并交付。')
        return event
