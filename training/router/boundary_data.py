"""第二组训练专用的决策边界样例对，不修改留出集标签。"""
import json
from collections import Counter
from .build_specification import candidate, pdf, state
from .contract import model_messages
from .dataset import ROOT, DatasetExample, canonical_input, digest, read_examples, write_jsonl
from .targeted_data import DEST as PREVIOUS
from .similarity import neighbours

DEST=ROOT/'boundary'


def additions():
    a=pdf('r_lumen','p_lumen','Lumen: Sparse Audio Coding')
    b=pdf('r_mosaic','p_mosaic','Mosaic: Robust Speech Reconstruction')
    c=pdf('r_delta','p_delta','Delta: Environmental Sound Detection')
    ca=candidate(a['paper_id'],a['title'])
    cb=candidate(b['paper_id'],b['title'])
    def s(goal,ready=(),found=(),done=()):
        return state(goal,pdfs=ready,candidates=found,results=done)
    pairs=[
        ('selection_context',[
            ('clarify',s('读一下我勾选的文章，讲清主要结论。',ready=[a,b]),'勾选记录确实缺失。'),
            ('answer',s('读一下我勾选的文章，讲清主要结论。',ready=[a,b],done=['用户勾选记录：r_mosaic。']),'results已明确选择，不能再次追问。')]),
        ('pair_context',[
            ('clarify',s('把收藏列表里我标星的两份研究做异同梳理。',ready=[a,b,c]),'三篇里哪两篇标星未知。'),
            ('compare',s('把收藏列表里我标星的两份研究做异同梳理。',ready=[a,b,c],done=['用户标星记录：r_lumen、r_delta。']),'两篇选择已由状态给出，交给参数阶段使用。')]),
        ('topic_context',[
            ('clarify',s('给我搜搜刚才讨论的话题有哪些新研究。'),'话题在输入中完全缺失。'),
            ('search',s('给我搜搜刚才讨论的话题有哪些新研究。',done=['用户此前确定主题：水下声学定位。']),'主题已有记录，无须重复确认。')]),
        ('candidate_context',[
            ('clarify',s('下载我标记的那条，随后解释算法。',found=[ca,cb]),'两个候选没有标记记录。'),
            ('download_pdf',s('下载我标记的那条，随后解释算法。',found=[ca,cb],done=['用户标记候选：p_lumen。']),'已有明确候选选择，应下载。')]),
        ('single_reference',[
            ('clarify',s('这份文献如何处理背景噪声？',ready=[a,b]),'单数指代对应两个可能对象。'),
            ('answer',s('这份文献如何处理背景噪声？',ready=[b]),'只有一篇，指代唯一。')]),
        ('whole_pair',[
            ('clarify',s('这两份文献在哪些假设上不同？',ready=[a,b,c]),'三篇中两篇不确定。'),
            ('compare',s('这两份文献在哪些假设上不同？',ready=[a,b]),'恰好两篇，指代已经完整。')]),
        ('default_search',[
            ('search',s('想了解流式语音增强领域，帮我找文献。'),'主题充分，篇数年份未指定不阻止搜索。'),
            ('search',s('想了解流式语音增强领域，帮我找三篇2024年以后的文献。'),'篇数年份具体化不改变动作。')]),
        ('default_dimension',[
            ('compare',s('横向梳理这些已上传研究。',ready=[a,b]),'可按问题、方法、证据等常见维度比较。'),
            ('compare',s('横向梳理这些已上传研究，重点看计算开销。',ready=[a,b]),'提供维度后仍是同一对比动作。')]),
        ('default_answer',[
            ('answer',s('帮我读懂Lumen。',ready=[a]),'有明确阅读目标，可先概述贡献和方法。'),
            ('answer',s('帮我读懂Lumen，重点解释编码器结构。',ready=[a]),'分析细节更具体，不需要重新获取或澄清。')]),
        ('named_retrieval',[
            ('search',s('找到Mosaic: Robust Speech Reconstruction并概括。'),'有明确可检索题名，不是用户必须补充的缺失信息。'),
            ('download_pdf',s('找到Mosaic: Robust Speech Reconstruction并概括。',found=[cb]),'同一目标已找到候选，下一步下载。')]),
        ('ready_reading',[
            ('download_pdf',s('请讲解Lumen的损失函数设计。',found=[ca]),'链接还不是可读全文。'),
            ('answer',s('请讲解Lumen的损失函数设计。',ready=[a],found=[ca]),'全文已经具备，直接回答。')]),
        ('ready_comparison',[
            ('download_pdf',s('Lumen和Mosaic在抗噪性上有什么区别？',ready=[a],found=[cb]),'缺第二篇全文，先获取。'),
            ('compare',s('Lumen和Mosaic在抗噪性上有什么区别？',ready=[a,b],found=[cb]),'两篇全文齐备。')]),
        ('completion',[
            ('answer',s('请概述Mosaic的适用条件。',ready=[b]),'还有尚未执行的目标。'),
            ('finish',s('请概述Mosaic的适用条件。',ready=[b],done=['Mosaic的适用条件已概述并给出原文依据。']),'当前要求已交付。')]),
        ('comparison_completion',[
            ('compare',s('梳理Lumen与Delta的输入约束。',ready=[a,c],done=['已分别整理两文的作者信息。']),'无关结果不能代替当前对比。'),
            ('finish',s('梳理Lumen与Delta的输入约束。',ready=[a,c],done=['已完成Lumen与Delta的输入约束对比并附证据。']),'目标对应的结果已完成。')]),
        ('english_default',[
            ('search',s('Collect literature on satellite cloud removal.')),
            ('search',s('Collect literature on satellite cloud removal; use reasonable defaults for year and count.'))]),
        ('english_pair',[
            ('compare',s('Contrast the uploaded studies.',ready=[a,b])),
            ('compare',s('Contrast the uploaded studies by computational cost.',ready=[a,b]))]),
        ('english_read',[
            ('answer',s('Help me understand the uploaded study.',ready=[b])),
            ('answer',s('Help me understand the assumptions in the uploaded study.',ready=[b]))]),
        ('ready_pair_stage',[
            ('download_pdf',s('读完Lumen和Delta后给出两者的局限对照。',ready=[c],found=[ca])),
            ('download_pdf',s('读完Lumen和Delta后给出两者的局限对照。',found=[ca,candidate(c['paper_id'],c['title'])]))]),
    ]
    result=[]
    for family,rows in pairs:
        for route,input_state,*reason in rows:
            identifier=f'boundary-{len(result)+1:03}'
            result.append(DatasetExample(id=identifier,parent_id=identifier,
                scenario_group='boundary_'+family,split='train',state=input_state,
                expected={'route':route},acceptable_routes=[route],
                rationale=reason[0] if reason else '任务与对象足够明确，采用合理默认值；有候选无全文时先下载。',
                tags=['training_only','decision_boundary_pair'],augmentation='none'))
    return result


def build():
    new=additions()
    combined=read_examples(PREVIOUS/'train.jsonl')+new
    reserved={k:read_examples(ROOT/f'{k}.jsonl') for k in ('validation','test_candidate')}
    reserved_rows=[e for rows in reserved.values() for e in rows]
    forbidden={canonical_input(e) for e in reserved_rows}
    goals={e.state.goal for e in reserved_rows}
    groups={e.scenario_group for e in reserved_rows}
    seen=set()
    for e in combined:
        key=canonical_input(e)
        if key in seen or key in forbidden or e.state.goal in goals or e.scenario_group in groups:
            raise ValueError(f'Duplicate or split overlap: {e.id}')
        seen.add(key)
    DEST.mkdir(parents=True,exist_ok=True)
    write_jsonl(DEST/'additions.jsonl',[e.model_dump() for e in new])
    write_jsonl(DEST/'train.jsonl',[e.model_dump() for e in combined])
    write_jsonl(DEST/'train.messages.jsonl',[{'messages':model_messages(e.state)+[
        {'role':'assistant','content':e.expected.model_dump_json()}]} for e in combined])
    pairs=[p for p in neighbours({'boundary':new,**reserved}) if p['left_split']=='boundary']
    manifest={'total_rows':len(combined),'added_rows':len(new),'prior_labels_changed':0,
        'class_counts':dict(Counter(e.expected.route for e in combined)),
        'train_sha256':digest(DEST/'train.jsonl'),
        'holdout_hashes':{k:digest(ROOT/f'{k}.jsonl') for k in reserved},
        'independent_review':'pending','near_pairs':pairs,
        'near_check_limitation':'Lexical screening only; validation-informed development, not independent evaluation.'}
    (DEST/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return manifest


if __name__=='__main__':
    print(json.dumps(build(),ensure_ascii=False,indent=2))
