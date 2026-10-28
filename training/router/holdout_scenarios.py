"""Legacy v1 scenarios. ALL are training/development data in v2.

Keep original IDs and definitions for reproducibility of the retirement ledger.
Never use these old split names as current held-out assignments.
"""
from .build_specification import candidate, pdf, state, failed_download


def seeds(split):
    a = pdf('r-a', 'p-a', 'Paper A')
    b = pdf('r-b', 'p-b', 'Paper B')
    c = pdf('r-c', 'p-c', 'Paper C')
    ca, cb = candidate('p-a','Paper A'), candidate('p-b','Paper B')
    # Validation combines explicit user constraints with resource availability.
    validation = [
        ('search','v_additional_literature',state('已有 Paper A 和 Paper B，另外找一篇不同的相关论文，先只返回链接',pdfs=[a,b]),'已有全文不是本次要求的新论文。'),
        ('search','v_unavailable_named',state('查找 Paper C 的全文，然后解释方法；不要分析 Paper A',pdfs=[a],candidates=[ca]),'指定对象C尚未找到，不能重复下载或分析A。'),
        ('download_pdf','v_one_of_three',state('比较 Paper A、Paper B 和 Paper C；A、B已上传，C也已经找到',pdfs=[a,b],candidates=[candidate('p-c','Paper C')]),'不能因为已有两篇就提前执行三篇对比。'),
        ('download_pdf','v_no_substitution',state('只分析 Paper B 的方法，不要用其他论文代替',pdfs=[a],candidates=[cb],remaining={'search':0}), '已有A不是目标；B公开且可下载。'),
        ('answer','v_excluded_compare',state('不要对比两篇，只读 Paper B 并解释其数据集',pdfs=[a,b]), '否定对比要求，目标是单篇问答。'),
        ('answer','v_search_already_done',state('找到 Paper A 后解释其训练过程',pdfs=[a],results=['已检索并下载Paper A，训练过程问题尚未回答']), '搜索子目标完成，但不能结束整个任务。'),
        ('compare','v_answer_budget_zero',state('Paper A、B都已上传，比较两者指标定义；不需要分别问答',pdfs=[a,b],remaining={'answer':0,'search':0}), '对比额度独立于问答额度。'),
        ('compare','v_extra_results',state('比较 Paper A 和 Paper B 的方法',pdfs=[a,b],results=['已完成Paper C的摘要；A与B尚未对比']), '其他论文结果不代表当前目标完成。'),
        ('finish','v_explicit_no_network',state('只用现有上传文件回答 Paper C 的问题；找不到就结束，不准联网',pdfs=[a,b]), '用户禁止获取缺失目标，结束并说明无法回答。'),
        ('finish','v_compare_unavailable',state('只要 Paper A 和 Paper B 的对比，不能用单篇问答替代；不能对比就停止',pdfs=[a,b],remaining={'compare':0}), '对比额度耗尽，其他工具不能完成限定目标。'),
        ('clarify','v_ambiguous_subset',state('从四篇里比较刚才选中的三篇',pdfs=[a,b,c,pdf('r-d','p-d','Paper D')]), '当前状态没有先前的选择信息。'),
        ('clarify','v_unspecified_baseline',state('比较 Paper A 与我的基线方法',pdfs=[a]), '用户的基线方法没有提供，不能随意选论文替代。'),
    ]
    # Test candidates are authored separately; only structural checks are run now.
    test = [
        ('search','t_more_than_found',state('Find four different papers about tool learning, links only.',candidates=[ca,cb]), '只有两个候选，仍未达到四篇目标。'),
        ('search','t_restart_topic',state('此前的工具学习任务结束了。现在帮我搜索图像压缩论文，只列链接',pdfs=[a,b],results=['已完成工具学习论文对比']), '旧任务结果不能终止新的检索目标。'),
        ('download_pdf','t_recover_different_id',state('读取 Paper B 并解释实验；Paper A失败不影响本任务',candidates=[ca,cb],outcomes=[failed_download('p-a')]), '不同候选未尝试，允许下载B。'),
        ('download_pdf','t_selected_fourth',state('四篇候选中我只想读 Paper D，再说明它的局限',pdfs=[a,b,c],candidates=[ca,cb,candidate('p-c','Paper C'),candidate('p-d','Paper D')]), '已有三篇不是指定的第四篇。'),
        ('answer','t_refute_user_claim',state('Paper A 是否真的证明方法比所有模型都好？请根据全文核实，不要比较其他论文',pdfs=[a,b]), '单篇证据核实不是多篇对比。'),
        ('answer','t_ordered_subtasks',state('先解释 Paper A 的消融实验，然后再比较A和B',pdfs=[a,b]), '显式先后顺序要求先问答。'),
        ('compare','t_ordered_after_answer',state('先解释 Paper A 的消融实验，然后再比较A和B',pdfs=[a,b],results=['已解释Paper A消融实验；A和B的比较尚未执行']), '先行问答已完成，下一步对比。'),
        ('compare','t_only_two_selected',state('Only compare the selected pair Paper B and Paper C; do not analyze Paper A.',pdfs=[a,b,c]), '额外PDF不改变已指定对比动作。'),
        ('finish','t_user_requests_stop',state('停止当前任务，不再搜索或分析，保留已经得到的结果',pdfs=[a,b],candidates=[ca,cb]), '用户明确停止，即使cancelled字段尚未同步也不得继续。'),
        ('finish','t_link_count_met',state('Find exactly two papers, links only; do not download or read.',candidates=[ca,cb],results=['Returned two distinct paper links.']), '只找两篇链接的任务已满足。'),
        ('clarify','t_private_context',state('根据导师昨天发我的要求选择论文',pdfs=[a,b]), '导师要求没有出现在当前上下文。'),
        ('clarify','t_same_title_unknown_choice',state('解释第二版 Paper A 的改进点',pdfs=[a,pdf('r-x',None,'Paper A')]), '同名文件没有版本标记，不能猜测哪个是第二版。'),
    ]
    if split == 'validation':
        return validation
    if split == 'test_candidate':
        return test
    raise ValueError(split)
