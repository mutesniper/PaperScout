"""仅向训练集补充反事实样例，现有 v2 数据划分保持字节一致。"""
import json
import random
from collections import Counter
from pathlib import Path

from .build_specification import candidate, pdf, state, failed_download
from .contract import model_messages
from .dataset import ROOT, DatasetExample, canonical_input, digest, read_examples, write_jsonl

DEST = ROOT/'targeted'


def additions():
    a,b = pdf('ra','pa','Atlas'),pdf('rb','pb','Beacon')
    ca,cb = candidate('pa','Atlas'),candidate('pb','Beacon')
    def s(goal,ready=(),found=(),done=(),budget=None,cancelled=False,failed=()):
        return state(goal,pdfs=ready,candidates=found,results=done,remaining=budget,
                     cancelled=cancelled,outcomes=failed)
    compare_goal = '仅对照Atlas与Beacon的实验结果，不要用单篇解释代替。'
    answer_goal = '请依据Atlas原文说明它的消融实验。'
    rows = [
        # 目标文本相同，仅改变结构化状态。
        ('cf_compare','search',s(compare_goal),'没有候选或全文，先查找。'),
        ('cf_compare','search',s(compare_goal,ready=[a]),'缺少Beacon候选，不能直接对比。'),
        ('cf_compare','download_pdf',s(compare_goal,found=[ca,cb]),'候选不等于全文，先下载。'),
        ('cf_compare','download_pdf',s(compare_goal,ready=[a],found=[ca,cb]),'Atlas已导入，只缺Beacon全文。'),
        ('cf_compare','compare',s(compare_goal,ready=[a,b]),'两篇就绪且尚未对比。'),
        ('cf_compare','finish',s(compare_goal,ready=[a,b],done=['Atlas与Beacon实验结果对比已交付']), '对比已交付，不重复。'),
        ('cf_compare','finish',s(compare_goal,ready=[a,b],cancelled=True),'取消状态优先于目标动词。'),
        ('cf_compare','finish',s(compare_goal,ready=[a,b],budget={'compare':0}),'对比额度耗尽且不允许问答替代。'),

        ('cf_read','search',s(answer_goal),'明确论文名但没有资料，可检索而不是追问。'),
        ('cf_read','download_pdf',s(answer_goal,found=[ca]),'只有链接，先获取原文。'),
        ('cf_read','download_pdf',s(answer_goal,ready=[b],found=[ca]),'已有Beacon不能回答Atlas问题。'),
        ('cf_read','answer',s(answer_goal,ready=[a]),'目标原文就绪。'),
        ('cf_read','finish',s(answer_goal,ready=[a],done=['Atlas消融实验已说明并附依据']), '已回答当前问题。'),
        ('cf_read','finish',s(answer_goal,ready=[a],budget={'steps':0}), '总步数耗尽，不能再问答。'),
        ('cf_reference','clarify',s('说明这份材料中的消融实验。'),'没有材料或论文名，指代不明确。'),
        ('cf_reference','clarify',s('说明这份材料中的消融实验。',ready=[a,b]),'有两份材料，没有指定是哪份。'),

        ('cf_selection','clarify',s('把我选中的研究拿来对照。',ready=[a,b,pdf('rc','pc','Cedar')]),'未提供选择记录，不能自行猜测。'),
        ('cf_selection','clarify',s('判断哪篇满足项目内部未公开的验收条件。',ready=[a,b]),'必须先提供内部条件。'),
        ('cf_selection','clarify',s('把论证和我的方案比一比。',ready=[a]),'用户自己的方案未给出。'),
        ('cf_selection','clarify',s('Atlas。',ready=[a]),'只有对象没有动作要求。'),
        ('cf_selection','answer',s('这份材料讲了什么消融实验？',ready=[a]),'唯一上传材料可确定，无须澄清。'),
        ('cf_selection','answer',s('两篇都保留，但现在只说Beacon的消融设计，不对照。',ready=[a,b]),'只问Beacon，不因两篇存在就比较。'),
        ('cf_selection','compare',s('对照Atlas与Beacon的消融设计，其余材料不处理。',ready=[a,b,pdf('rc','pc','Cedar')]),'明确指定的两篇已经导入。'),
        ('cf_selection','finish',s('两篇都保留，但现在只说Beacon的消融设计，不对照。',ready=[a,b],done=['Beacon消融设计已回答']), '当前单篇要求已完成。'),

        ('cf_budget','finish',s('检索关于机器人抓取的资料。',budget={'search':0}), '无资料且搜索预算为0，停止并说明受阻。'),
        ('cf_budget','finish',s('阅读Atlas后解释实验，取不到全文就结束。',found=[ca],budget={'download_pdf':0}), '不能绕过下载额度。'),
        ('cf_budget','finish',s('只依据Atlas回答，不得用搜索或对比替代。',ready=[a,b],budget={'answer':0}), '指定问答额度耗尽。'),
        ('cf_budget','finish',s('检索关于机器人抓取的资料。',cancelled=True), '取消后即使有预算也不能搜索。'),
        ('cf_budget','answer',s('依据Atlas回答消融实验。',ready=[a],budget={'search':0,'download_pdf':0}), '已有全文，其他工具预算耗尽不影响问答。'),
        ('cf_budget','compare',s('对照Atlas和Beacon的消融实验。',ready=[a,b],budget={'search':0,'download_pdf':0,'answer':0}), '仍有独立对比额度。'),
        ('cf_budget','download_pdf',s('Atlas已找到，请阅读后回答。',found=[ca],budget={'search':0}), '不必重新搜索，下载额度仍可用。'),
        ('cf_budget','search',s('只找机器人抓取研究的网页链接。',budget={'download_pdf':0,'answer':0,'compare':0}), '链接任务只需要可用搜索额度。'),

        ('cf_progress','search',s('先说明Atlas，再检索机器人抓取研究。',ready=[a],done=['Atlas说明已交付']), '第一项完成，还有检索目标。'),
        ('cf_progress','search',s('找可读的机器人抓取研究，必要时换候选。',found=[ca],failed=[failed_download('pa')]), '唯一候选已失败，应寻找替代。'),
        ('cf_progress','download_pdf',s('先说明Atlas，再解释Beacon消融。',ready=[a],found=[cb],done=['Atlas说明已交付']), '剩余Beacon任务需要先下载。'),
        ('cf_progress','download_pdf',s('先取得两篇全文，再对照，不要提前进行。',ready=[a],found=[ca,cb]), '只导入一篇，需下载第二篇。'),
        ('cf_progress','answer',s('先解释Atlas，再对照Atlas与Beacon。',ready=[a,b]), '遵守先问答的顺序。'),
        ('cf_progress','answer',s('先对照Atlas与Beacon，再解释Beacon消融。',ready=[a,b],done=['Atlas与Beacon对照已交付']), '对比完成但最后单篇任务仍未完成。'),
        ('cf_progress','compare',s('先解释Atlas，再对照Atlas与Beacon。',ready=[a,b],done=['Atlas解释已交付']), '第一项已做，继续对比。'),
        ('cf_progress','finish',s('先解释Atlas，再对照Atlas与Beacon。',ready=[a,b],done=['Atlas解释已交付','Atlas与Beacon对照已交付']), '全部子目标完成。'),

        ('cf_english','clarify',s('Continue the research direction I discussed in another conversation.')), 
        ('cf_english','clarify',s('Compare these with my unpublished design.',ready=[a,b])),
        ('cf_english','clarify',s('Read the item I selected on my phone.',found=[ca,cb])),
        ('cf_english','clarify',s('Use the acceptance threshold I have not supplied.',ready=[a])),
        ('cf_english','search',s('Find papers on acoustic echo cancellation. Links are sufficient.')),
        ('cf_english','download_pdf',s('Explain the ablations in Atlas, using its full text.',found=[ca])),
        ('cf_english','answer',s('Explain the ablations in Atlas, using its full text.',ready=[a])),
        ('cf_english','finish',s('Explain the ablations in Atlas, using its full text.',ready=[a],done=['Atlas ablation explanation delivered with evidence.'])),
    ]
    english_reasons = ['缺少另一对话的研究方向。','缺少用户未发表设计。','手机上的选择未提供。','没有必须遵守的阈值。',
                       '明确主题可直接检索。','只有候选，需要全文。','目标全文已导入。','所需答案已交付。']
    result=[]
    for i,row in enumerate(rows):
        family,route,input_state,*why=row
        identifier=f'targeted-{i+1:03}'
        result.append(DatasetExample(id=identifier,parent_id=identifier,scenario_group=family,
            split='train',state=input_state,expected={'route':route},acceptable_routes=[route],
            rationale=why[0] if why else english_reasons[i-40],
            tags=['training_only','counterfactual_state','baseline_error_category'],augmentation='none'))
    return result


def build():
    base=read_examples(ROOT/'train.jsonl')
    new=additions()
    combined=base+new
    seen=set()
    reserved=[e for split in ('validation','test_candidate') for e in read_examples(ROOT/f'{split}.jsonl')]
    reserved_inputs={canonical_input(e) for e in reserved}
    reserved_groups={e.scenario_group for e in reserved}
    reserved_goals={e.state.goal for e in reserved}
    for e in combined:
        key=canonical_input(e)
        if key in seen or key in reserved_inputs or e.scenario_group in reserved_groups or e.state.goal in reserved_goals:
            raise ValueError(f'Duplicate/cross-split leakage: {e.id}')
        seen.add(key)
    DEST.mkdir(parents=True,exist_ok=True)
    write_jsonl(DEST/'additions.jsonl',[e.model_dump() for e in new])
    write_jsonl(DEST/'train.jsonl',[e.model_dump() for e in combined])
    write_jsonl(DEST/'train.messages.jsonl',[{'messages':model_messages(e.state)+[
        {'role':'assistant','content':e.expected.model_dump_json()}]} for e in combined])
    manifest={'version':'targeted-train-v1','base_rows':len(base),'added_rows':len(new),'total_rows':len(combined),
        'class_counts':dict(Counter(e.expected.route for e in combined)),
        'basis':'Training-side errors and general resource/clarification/progress constraints; not test predictions',
        'independent_review':'pending','holdout_hashes':{s:digest(ROOT/f'{s}.jsonl') for s in ('validation','test_candidate')},
        'files':{p.name:digest(p) for p in sorted(DEST.glob('*.jsonl'))}}
    (DEST/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return manifest


def smoke_examples(rows, per_class=8):
    """分层且确定性的工程探测样例，不根据模型得分选择。"""
    from .contract import ROUTES
    rng=random.Random(20261028)
    selected=[]
    for route in ROUTES:
        old=[e for e in rows if e.expected.route==route and not e.id.startswith('targeted-')]
        new=[e for e in rows if e.expected.route==route and e.id.startswith('targeted-')]
        rng.shuffle(old); rng.shuffle(new)
        chosen=new[:min(6,len(new))]
        chosen+=old[:per_class-len(chosen)]
        if len(chosen)!=per_class:
            raise ValueError('Insufficient examples for balanced smoke')
        selected.extend(chosen)
    rng.shuffle(selected)
    return selected


if __name__=='__main__':
    print(json.dumps(build(),ensure_ascii=False,indent=2))
