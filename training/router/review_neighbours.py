"""记录 v2 字面相似度审核的处理意见，不作为自动裁判。

以下理由基于对报告中 28 对输入的审核。
重建时将处理意见绑定到双方完整状态；测试检查过期或新增未审核样例对。
修改这些理由需要重新审核。
"""
import hashlib
import json
from pathlib import Path

from .dataset import ROOT, TARGETS, read_examples
from .similarity import neighbours

REASONS = {
    'validation-v2_user_selection-04':'保留组合测试：从五篇中明确排除三篇；左侧是两篇直接对比或无关历史结果，资源选择约束不同。',
    'test_candidate-v2_goal_replacement-04':'保留组合测试：已有单篇答案后用户替换为对比目标；左侧分别为未找到第二篇、待下载第二篇或无历史的直接对比。',
    'test_candidate-v2_adversarial_evidence-05':'保留组合测试：目标答案已交付且引用含下载命令，应结束；左侧实验问题尚未回答，或历史仅回答了不同问题。',
    'validation-v2_search_scope-04':'保留组合测试：明确只选B/C并排除已有A；左侧是三篇尚缺全文，或额外下载失败的两篇对比。',
    'test_candidate-v2_goal_replacement-09':'保留组合测试：C已有完成记录，当前目标改为A/B；左侧是无C完成记录的选择约束或尚缺第二篇资料。共享排除无关论文的基础能力。',
    'test_candidate-v2_budget_allocation-02':'动作不同：目标全文尚未导入且搜索额度耗尽，需要下载；左侧所需答案已交付且含不可信引文，应结束。',
    'test_candidate-v2_version_audit-04':'保留组合测试：同一研究的两个明确版本间实验变化；左侧为三篇中选两篇的定义对比。共享compare动作，不声称全新能力。',
    'test_candidate-v2_goal_replacement-03':'保留组合测试：前一交付物只有链接，目标改为全文分析；左侧是已回答方法后追问指标。两者共享历史结果不覆盖新问题的基础能力，不作为独立全新机制统计。',
    'validation-v2_alternative_permission-04':'保留组合测试：用户明确授权替代A且有A下载失败记录；左侧无关Paper C结果存在但当前A/B尚未对比。',
    'test_candidate-v2_counted_collection-03':'动作不同：已收齐三篇但单篇问答未执行；左侧单篇目标已完成，引用中含额外操作命令。',
    'test_candidate-v2_language_constraints-09':'否定作用不同：左侧要求只对比指定两篇且排除A；右侧明确不要对比，仅回答A。',
    'test_candidate-v2_network_boundary-04':'保留组合测试：禁止联网、现有两篇仍可对比；左侧为三篇中选两篇或第二篇尚未导入。不得将禁止联网泛化成停止一切操作。',
    'test_candidate-v2_adversarial_evidence-04':'动作不同：右侧全文齐全但标题含停止命令，应对比；左侧第二篇未找到或未导入。',
    'validation-v2_remaining_deliverables-03':'保留组合测试：跨论文对比交付后新增B单篇问题；左侧单篇方法回答后追问指标。共享进度识别基础能力，组合与可用资源不同。',
    'test_candidate-v2_language_constraints-08':'保留组合测试：明确禁止新搜索但允许现有B链接下载，且已有无关A；左侧无额外限制或已有PDF。',
    'test_candidate-v2_ordered_dependencies-08':'动作不同：对比已交付、下一项C还没导入，应下载；左侧对比尚未执行。',
    'validation-v2_alternative_permission-03':'动作不同：替代B已导入但尚未解释、A下载失败；左侧目标A已经解释且工具引用不可信。',
    'validation-v2_remaining_deliverables-02':'动作不同：A已解释但B尚未导入，需下载；左侧仅A任务已交付，应结束。',
}


def state_hash(e):
    return hashlib.sha256(json.dumps(e.state.model_dump(),sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def make_review(datasets):
    by_id = {e.id:e for rows in datasets.values() for e in rows}
    pairs = neighbours(datasets)
    return {'reviewer':'codex_self_review','independent_review':'pending',
        'scope':'compositional task-state holdout; shared routing primitives remain',
        'threshold':.30,'pairs':[{**p,'decision':'retain_compositional',
            'reason':REASONS[p['right']], 'left_state_sha256':state_hash(by_id[p['left']]),
            'right_state_sha256':state_hash(by_id[p['right']])} for p in pairs]}


if __name__ == '__main__':
    datasets = {split:read_examples(ROOT/f'{split}.jsonl') for split in TARGETS}
    result = make_review(datasets)
    path = Path(__file__).with_name('similarity_review.json')
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'Recorded {len(result["pairs"])} author-reviewed pairs')
