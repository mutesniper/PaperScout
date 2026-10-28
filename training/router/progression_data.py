"""Training-only task progress boundaries and a frozen, separately authored probe.

The probe is synthetic/self-reviewed, not an independent human benchmark.
Neither legacy validation labels nor the unopened test candidate split change.
"""
import json
from collections import Counter

from .build_specification import candidate, pdf, state, failed_download
from .contract import model_messages
from .dataset import ROOT, DatasetExample, canonical_input, digest, read_examples, write_jsonl
from .boundary_data import DEST as PREVIOUS
from .similarity import neighbours

DEST = ROOT / 'progression'


def examples(groups, split, prefix):
    result = []
    for family, rows in groups:
        for route, input_state, rationale in rows:
            identifier = f'{prefix}-{len(result)+1:03}'
            result.append(DatasetExample(
                id=identifier, parent_id=identifier, scenario_group=f'{prefix}_{family}',
                split=split, state=input_state, expected={'route': route},
                acceptable_routes=[route], rationale=rationale,
                tags=['authored_state', 'task_progress', 'independent_review_pending'], augmentation='none'))
    return result


def additions():
    a = pdf('r_maple', 'p_maple', 'Maple: Battery Health Estimation')
    b = pdf('r_cedar', 'p_cedar', 'Cedar: Battery Lifetime Prediction')
    c = pdf('r_kite', 'p_kite', 'Kite: Traffic Signal Optimization')
    ca, cb = candidate(a['paper_id'], a['title']), candidate(b['paper_id'], b['title'])
    s = state
    return examples([
        ('pair_missing', [
            ('search', s('拿Maple与Cedar做寿命预测误差对照，必须两篇齐全再分析。', pdfs=[a]), '第二个对象已明确，可搜索而不能拿一篇代替两篇。'),
            ('download_pdf', s('拿Maple与Cedar做寿命预测误差对照，必须两篇齐全再分析。', pdfs=[a], candidates=[cb]), '缺少对象已有可下载候选。'),
            ('compare', s('拿Maple与Cedar做寿命预测误差对照，必须两篇齐全再分析。', pdfs=[a,b]), '目标所需全文齐备。'),
            ('finish', s('拿Maple与Cedar做寿命预测误差对照，必须两篇齐全再分析。', pdfs=[a,b], results=['已交付两篇的寿命预测误差对照与证据。']), '完整目标已经交付。'),
        ]),
        ('ordered_read', [
            ('answer', s('先解释Maple的假设，然后才能比较Maple和Cedar。', pdfs=[a,b]), '有两个PDF也应先执行单篇子目标。'),
            ('compare', s('先解释Maple的假设，然后才能比较Maple和Cedar。', pdfs=[a,b], results=['已解释Maple的假设。']), '先决子目标完成，进入对比。'),
            ('answer', s('先对比这两篇，再解释Cedar的适用边界。', pdfs=[a,b], results=['两篇对比已完成。']), '还有指定单篇问题未完成。'),
            ('finish', s('先对比这两篇，再解释Cedar的适用边界。', pdfs=[a,b], results=['两篇对比已完成。','Cedar适用边界已解释。']), '两个交付物均已提供。'),
        ]),
        ('selection_gate', [
            ('clarify', s('从这些上传文献中，只对比我稍后选中的两篇，先等我决定。', pdfs=[a,b,c]), '用户明确保留选择权，当前缺少选择。'),
            ('compare', s('从这些上传文献中，只对比我稍后选中的两篇，先等我决定。', pdfs=[a,b,c], results=['用户已决定选择Maple与Cedar。']), '选择已补充，不应反复澄清。'),
            ('clarify', s('解释我收藏的那篇文章的方法。', pdfs=[a,b]), '没有收藏对应关系且对象不唯一。'),
            ('answer', s('解释我收藏的那篇文章的方法。', pdfs=[a,b], results=['用户收藏条目：p_cedar。']), '收藏对象已经给出。'),
        ]),
        ('new_deliverable', [
            ('search', s('讲完Maple以后，继续找电池健康估计的综述，只需返回链接。', pdfs=[a], results=['Maple已讲解。']), '讲解完成不能代替后续检索。'),
            ('answer', s('讲完Maple以后，继续找电池健康估计的综述，只需返回链接。', pdfs=[a]), '用户要求先讲已有文章。'),
            ('finish', s('讲完Maple以后，继续找电池健康估计的综述，只需返回链接。', pdfs=[a], results=['Maple已讲解。','已返回电池健康估计综述的链接。']), '全部要求完成。'),
            ('search', s('之前已经介绍过Maple，这次另找电池回收方面的论文。', pdfs=[a], results=['Maple方法介绍已完成。']), '旧成果不满足新的研究主题。'),
        ]),
        ('replacement_permission', [
            ('finish', s('只读Maple，下载不成功就到此为止，不换文章。', candidates=[ca,cb], outcomes=[failed_download('p_maple')]), '用户禁止替换，应受阻停止。'),
            ('download_pdf', s('Maple下载失败可以换Cedar，读一篇即可。', candidates=[ca,cb], outcomes=[failed_download('p_maple')]), '替换已获许可且有候选。'),
            ('answer', s('Maple不再需要了，请解释已上传Cedar的评测方法。', pdfs=[b], outcomes=[failed_download('p_maple')]), '无关失败不阻止新目标。'),
            ('search', s('找一篇能公开阅读的电池研究，下载失败可换其他论文。', candidates=[ca], outcomes=[failed_download('p_maple')]), '已尝试候选耗尽，但仍可搜索。'),
        ]),
        ('topic_vs_context', [
            ('search', s('找城市道路信号控制的研究，用默认年份范围即可。', pdfs=[a], candidates=[ca]), '电池资料不属于目标领域。'),
            ('clarify', s('找导师上次说的那个方向，聊天记录里没有写具体是什么。', pdfs=[a]), '不可从偶然存在的PDF推断私人主题。'),
            ('search', s('找导师上次说的那个方向。', results=['导师指定方向：锂电池热失控预警。']), '先前记录明确主题。'),
            ('answer', s('先不用找新的，解释现有Maple里健康指标的定义。', pdfs=[a]), '用户明确只读现有论文。'),
        ]),
        ('failed_search', [
            ('search', s('收集电池寿命预测论文链接。', outcomes=[{'operation':'search','status':'failed','detail':'第一次检索超时，无结果。'}], remaining={'search':1}), '还有搜索机会。'),
            ('finish', s('收集电池寿命预测论文链接。', outcomes=[{'operation':'search','status':'failed','detail':'检索超时，无结果。'}], remaining={'search':0}), '没有候选，搜索预算耗尽。'),
            ('download_pdf', s('读Cedar并概括。', candidates=[cb], remaining={'search':0}), '搜索用尽不影响已有候选下载。'),
            ('answer', s('读Cedar并概括。', pdfs=[b], remaining={'search':0,'download_pdf':0}), '准备完成，无需搜索和下载。'),
        ]),
        ('completion_not_metadata', [
            ('answer', s('解释Maple的主要实验结论。', pdfs=[a], results=['已列出Maple作者、年份和下载地址。']), '元数据不是实验结论。'),
            ('compare', s('对照Maple和Cedar的数据集。', pdfs=[a,b], results=['两篇PDF已经保存。']), '下载成功不是对比完成。'),
            ('finish', s('只给我Maple的下载地址。', candidates=[ca], results=['Maple下载地址已返回。']), '用户仅要求链接。'),
            ('search', s('只给我Kite: Traffic Signal Optimization的下载地址。', candidates=[ca], results=['Maple下载地址已返回。']), '已有链接属于另一个对象。'),
        ]),
        ('english_order', [
            ('answer', s('Explain Cedar first; only then contrast it with Maple.', pdfs=[a,b]), 'First requested artifact is an explanation.'),
            ('compare', s('Explain Cedar first; only then contrast it with Maple.', pdfs=[a,b], results=['Cedar explanation delivered.']), 'The prerequisite explanation is done.'),
            ('search', s('Compare Maple with Kite after obtaining both full texts.', pdfs=[a]), 'The named second paper needs retrieval.'),
            ('finish', s('Explain Cedar first; only then contrast it with Maple.', pdfs=[a,b], results=['Cedar explanation delivered.','Cedar versus Maple comparison delivered.']), 'Both outputs have been delivered.'),
        ]),
        ('followup_context', [
            ('clarify', s('接着做剩下的，但我的待办清单没有发给你。', pdfs=[a], results=['Maple方法已介绍。']), '剩余任务本身未知，不能自造搜索目标。'),
            ('search', s('接着做剩下的。', pdfs=[a], results=['待办：讲解Maple，再找交通预测文献。','Maple讲解已完成。']), '记录包含未完成的明确检索目标。'),
            ('compare', s('接着做剩下的。', pdfs=[a,b], results=['待办：讲解Maple，再对比Maple与Cedar。','Maple讲解已完成。']), '记录明确剩余对比任务。'),
            ('finish', s('接着做剩下的。', pdfs=[a,b], results=['待办：对比Maple与Cedar。','Maple与Cedar对比已完成。']), '记录中的清单已完成。'),
        ]),
        ('literal_stop', [
            ('finish', s('不用继续了，保持当前结果，别再下载。', candidates=[ca,cb]), '自然语言明确停止，即便cancelled标志尚未设置。'),
            ('download_pdf', s('别再搜索了，下载已找到的Maple后讲解。', candidates=[ca]), '只停止搜索，不是停止整个任务。'),
            ('answer', s('别对比了，只介绍Maple的方法。', pdfs=[a,b]), '取消对比但保留明确单篇任务。'),
            ('search', s('不用研究Maple了，改找车流量预测文献。', pdfs=[a]), '取消旧目标，不是取消新的检索要求。'),
        ]),
        ('public_access', [
            ('search', s('找一篇公开全文的电池健康论文并解释方法。', candidates=[candidate('p_maple',a['title'],False)]), '唯一候选没有可下载全文，可另找。'),
            ('download_pdf', s('找一篇公开全文的电池健康论文并解释方法。', candidates=[candidate('p_maple',a['title'],False),cb]), '有可用公开候选。'),
            ('finish', s('找一篇公开全文的电池健康论文并解释方法。', candidates=[candidate('p_maple',a['title'],False)], remaining={'search':0}), '无可用全文且不能继续搜索。'),
            ('clarify', s('帮我解释老师私下发的未公开手稿，我还没上传。'), '必须由用户提供私人文献。'),
        ]),
        ('budget_and_scope', [
            ('search', s('我只要新的电池材料文献链接，不读取PDF。', remaining={'download_pdf':0,'answer':0,'compare':0}), '检索预算仍在，其他预算无关。'),
            ('compare', s('比较Maple和Cedar的实验设置。', pdfs=[a,b], remaining={'search':0,'download_pdf':0,'answer':0}), '对比所需资源和预算齐全。'),
            ('finish', s('比较Maple和Cedar的实验设置。', pdfs=[a,b], remaining={'compare':0}), '指定对比工具预算耗尽，不能冒充完成。'),
            ('answer', s('介绍Maple，不做对比。', pdfs=[a,b], remaining={'compare':0}), '对比预算不影响单篇分析。'),
        ]),
        ('which_paper', [
            ('clarify', s('把那篇下载下来讲讲。', candidates=[ca,cb]), '两个候选且指代不明确。'),
            ('download_pdf', s('把那篇下载下来讲讲。', candidates=[ca,cb], results=['用户指的是Cedar。']), '指代已在上下文中指定。'),
            ('clarify', s('这三篇里的两篇做对比，我还没决定选哪两篇。', pdfs=[a,b,c]), '选择尚未完成。'),
            ('compare', s('这三篇里的两篇做对比。', pdfs=[a,b,c], results=['用户已选Maple与Kite。']), '有明确两篇选择。'),
        ]),
        ('named_target_vs_unknown', [
            ('search', s('帮我找Kite: Traffic Signal Optimization，然后解释它。', pdfs=[a,b]), '已知完整题名，不需要用户上传或澄清。'),
            ('clarify', s('帮我找我昨天线下听说的那篇论文，我没给题名或主题。'), '没有任何可确定的检索目标。'),
            ('search', s('Maple资料已经有了，还差Cedar，先把缺的论文找来。', pdfs=[a], candidates=[ca]), '候选中只有已导入对象，不能重复下载。'),
            ('download_pdf', s('Maple资料已经有了，还差Cedar，先把缺的论文找来。', pdfs=[a], candidates=[ca,cb]), '已有缺失对象的候选。'),
        ]),
    ], 'train', 'progression')


def probe():
    """New tasks, fixed before training; never used for checkpoint selection."""
    p = pdf('r_iriscope','p_iriscope','Iriscope: Retinal Vessel Segmentation')
    q = pdf('r_optima','p_optima','Optima: Fundus Image Restoration')
    r = pdf('r_clinic','p_clinic','Clinic: Clinical Note Coding')
    cp, cq = candidate(p['paper_id'],p['title']), candidate(q['paper_id'],q['title'])
    s = state
    return examples([
        ('reference_handoff', [
            ('search', s('我要一份视网膜分割文献清单，暂不读全文。'), '主题已给，可采用默认范围。'),
            ('download_pdf', s('请读Iriscope并说明标注方式。', candidates=[cp]), '目标候选可读但尚未导入。'),
            ('answer', s('请读Iriscope并说明标注方式。', pdfs=[p]), '全文已具备。'),
            ('compare', s('这两篇眼底影像论文各自依赖什么标注？', pdfs=[p,q]), '恰好两个对象且问题明确。'),
            ('finish', s('我要一份视网膜分割文献清单，暂不读全文。', candidates=[cp,cq], results=['文献清单与对应链接已交付。']), '仅要清单，已交付。'),
            ('clarify', s('请读我笔记中圈起来的文章。', pdfs=[p,q]), '笔记圈选对应关系缺失。'),
        ]),
        ('access_recovery', [
            ('search', s('任何公开的眼底图像研究都行，读到一篇后概述。', candidates=[cp], outcomes=[failed_download('p_iriscope')]), '当前下载失败，还能搜索其他对象。'),
            ('download_pdf', s('任何公开的眼底图像研究都行，读到一篇后概述。', candidates=[cp,cq], outcomes=[failed_download('p_iriscope')]), '仍有未尝试候选。'),
            ('answer', s('既然Optima全文到了，就解释Optima，不必再管Iriscope。', pdfs=[q], outcomes=[failed_download('p_iriscope')]), '新目标具备证据。'),
            ('compare', s('只比较已经上传的两篇眼底研究。', pdfs=[p,q], outcomes=[failed_download('p_clinic')]), '无关失败不影响指定目标。'),
            ('finish', s('本次仅限Iriscope，拿不到原文就结束，不要另找替代。', candidates=[cp,cq], outcomes=[failed_download('p_iriscope')]), '遵守不可替换限制。'),
            ('clarify', s('阅读医院内部那份报告，外网查不到，我尚未提供文件。'), '私人资料必须由用户提供。'),
        ]),
        ('deliverable_sequence', [
            ('search', s('解释完Iriscope后，再给我眼底修复文献的链接。', pdfs=[p], results=['Iriscope解释已交付。']), '第二个交付物尚未完成。'),
            ('download_pdf', s('解释完Iriscope后再讲Optima。', pdfs=[p], candidates=[cq], results=['Iriscope解释已交付。']), '下一篇已有候选但没有全文。'),
            ('answer', s('先解释Optima的噪声假设，之后再对比这两篇；不要跳过前一步。', pdfs=[p,q]), '应先讲解而不是直接对比。'),
            ('compare', s('先解释Optima的噪声假设，之后再对比这两篇；不要跳过前一步。', pdfs=[p,q], results=['Optima噪声假设已讲解。']), '先决任务已完成。'),
            ('finish', s('先解释Optima的噪声假设，之后再对比这两篇；不要跳过前一步。', pdfs=[p,q], results=['Optima噪声假设已讲解。','两篇对比已交付。']), '所有交付完成。'),
            ('clarify', s('继续完成我没告诉你的那些待办。', pdfs=[p], results=['Iriscope已读完。']), '不能猜测未提供的待办。'),
        ]),
        ('scope_switch', [
            ('search', s('换个方向，查临床笔记编码的文献，现有资料只有眼底影像。', pdfs=[p,q]), '现有资料不覆盖新主题。'),
            ('download_pdf', s('不要继续检索了，把Optima全文取来。', candidates=[cp,cq]), '停止检索但仍要求下载指定对象。'),
            ('answer', s('对比先取消，改为只解释Iriscope的损失函数。', pdfs=[p,q]), '新指令是单篇问答。'),
            ('compare', s('这些文献中仅比较Iriscope与Clinic，排除Optima。', pdfs=[p,q,r]), '选择明确，参数交由下游解析。'),
            ('finish', s('就到这里吧，不再执行任何研究操作。', pdfs=[p,q], candidates=[cq]), '用户明确终止。'),
            ('clarify', s('研究方向改成我上周面谈说的那个，没在系统记录。', pdfs=[p,q]), '新主题没有可用上下文。'),
        ]),
        ('capacity_boundaries', [
            ('search', s('给出医学影像标注的论文链接。', remaining={'download_pdf':0,'answer':0,'compare':0}), '链接任务只需要搜索。'),
            ('download_pdf', s('概述Iriscope的训练数据。', candidates=[cp], remaining={'search':0}), '已找到候选，无需搜索预算。'),
            ('answer', s('概述Iriscope的训练数据。', pdfs=[p], remaining={'search':0,'download_pdf':0,'compare':0}), '现有全文支持问答。'),
            ('compare', s('比较现有两篇的训练数据。', pdfs=[p,q], remaining={'search':0,'download_pdf':0,'answer':0}), '对比预算尚在。'),
            ('finish', s('比较现有两篇的训练数据。', pdfs=[p,q], remaining={'steps':0}), '步数耗尽，只能停止。'),
            ('clarify', s('只解释我标成优先的文献，但优先标记尚未同步。', pdfs=[p,q], remaining={'search':0}), '资源存在不代表对象已指定。'),
        ]),
        ('english_selection', [
            ('search', s('Obtain both Iriscope and Optima before comparing; only Iriscope is here.', pdfs=[p]), 'Known missing paper can be searched.'),
            ('download_pdf', s('Read the chosen result.', candidates=[cp,cq], results=['User chose Optima.']), 'Selection supplied; download it.'),
            ('answer', s('Explain the selected paper.', pdfs=[p,q], results=['Selection confirmed: Iriscope.']), 'Selection supplied; answer directly.'),
            ('compare', s('Contrast my selected pair.', pdfs=[p,q,r], results=['Selected pair: Optima and Clinic.']), 'Both selected PDFs are available.'),
            ('finish', s('Explain Iriscope.', pdfs=[p], results=['Explanation of Iriscope delivered with evidence.']), 'Requested answer delivered.'),
            ('clarify', s('Contrast my selected pair; I have not chosen them yet.', pdfs=[p,q,r]), 'User has not selected the pair.'),
        ]),
    ], 'validation', 'progression_probe')


def build():
    new, audit = additions(), probe()
    combined = read_examples(PREVIOUS/'train.jsonl') + new
    legacy = {k: read_examples(ROOT/f'{k}.jsonl') for k in ('validation','test_candidate')}
    partitions = {'train':combined, 'probe':audit, **legacy}
    seen, groups = {}, {}
    for split, rows in partitions.items():
        for e in rows:
            key = canonical_input(e)
            if key in seen:
                raise ValueError(f'Duplicate model input: {e.id} / {seen[key]}')
            seen[key] = e.id
            if e.scenario_group in groups and groups[e.scenario_group] != split:
                raise ValueError('Task group crosses splits')
            groups[e.scenario_group] = split
    DEST.mkdir(parents=True, exist_ok=True)
    for name, rows in [('additions',new),('train',combined),('probe',audit)]:
        write_jsonl(DEST/f'{name}.jsonl', [e.model_dump() for e in rows])
    write_jsonl(DEST/'train.messages.jsonl', [{'messages':model_messages(e.state)+[
        {'role':'assistant','content':e.expected.model_dump_json()}]} for e in combined])
    # Flag lexical neighbours for review, not a claim of semantic independence.
    near = neighbours({'new_train':new, 'probe':audit, **legacy})
    manifest = {'total_rows':len(combined), 'added_rows':len(new), 'probe_rows':len(audit),
        'prior_labels_changed':0, 'class_counts':dict(Counter(e.expected.route for e in combined)),
        'probe_class_counts':dict(Counter(e.expected.route for e in audit)),
        'train_sha256':digest(DEST/'train.jsonl'), 'probe_sha256':digest(DEST/'probe.jsonl'),
        'holdout_hashes':{k:digest(ROOT/f'{k}.jsonl') for k in legacy},
        'independent_review':'pending', 'near_pairs':near,
        'probe_role':'Frozen before training; evaluated once after selection; synthetic authored diagnostic, not independent human gold.',
        'selection_data':'legacy validation only; never probe or test_candidate'}
    (DEST/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return manifest


if __name__ == '__main__':
    print(json.dumps(build(),ensure_ascii=False,indent=2))
