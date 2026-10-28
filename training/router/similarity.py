"""只读的跨数据划分近邻审核，不是模型评测。

名称和前缀归一化后的字符二元组重合度仅辅助审核，不能证明语义独立。
仅报告输入文本，不报告模型预测。
"""
import argparse
import json
import re
from itertools import combinations
from pathlib import Path

from .dataset import PREFIXES, ROOT, TARGETS, read_examples


def goal_tokens(text):
    for prefix in PREFIXES[1:]:
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    text = re.sub(r'Paper\s*[A-E]|TinyAgent|Toolformer|Gorilla', '论文对象', text, flags=re.I)
    text = re.sub(r'(?<![a-zA-Z])[A-E](?![a-zA-Z])', '论文对象', text)
    text = re.sub(r'[^\w]', '', text.casefold())
    return {text[i:i+2] for i in range(len(text)-1)}


def signature(e):
    s = e.state
    return (len(s.available_pdfs),len(s.candidates),bool(s.results),
        bool(s.recent_outcomes),tuple(k for k,v in s.remaining.model_dump().items() if v==0),s.cancelled)


def neighbours(datasets, threshold=.30):
    tokens = {e.id:goal_tokens(e.state.goal) for rows in datasets.values() for e in rows}
    result = []
    for (split_a,rows_a),(split_b,rows_b) in combinations(datasets.items(),2):
        for a in rows_a:
            for b in rows_b:
                aa,bb = tokens[a.id],tokens[b.id]
                similarity = len(aa&bb)/len(aa|bb) if aa|bb else 1
                if similarity >= threshold:
                    result.append({'left':a.id,'right':b.id,'left_split':split_a,'right_split':split_b,
                        'goal_similarity':round(similarity,4),'same_resource_shape':signature(a)==signature(b),
                        'left_goal':a.state.goal,'right_goal':b.state.goal})
    return sorted(result,key=lambda r:(-r['goal_similarity'],r['left'],r['right']))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=Path('outputs/router/v2-similarity.json'))
    args = parser.parse_args()
    datasets = {split:read_examples(ROOT/f'{split}.jsonl') for split in TARGETS}
    pairs = neighbours(datasets)
    report = {'method':'normalized_goal_bigram_jaccard','threshold':.30,
        'guarantee':'lexical review aid only, not semantic independence proof',
        'model_calls':0,'pairs':pairs}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'pairs':len(pairs),'threshold':.30,'output':str(args.output),
        'note':'Review full states; lexical overlap is not semantic equivalence.'},ensure_ascii=False,indent=2))
