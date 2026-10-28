"""冻结的简单启发式规则，不读取标签或样例 ID。

仅作为参考基线，不用作应用路由。这里不处理宽泛的标题相关性、
多目标完成判断或有歧义的指代。
"""
import re

from .contract import State, allowed_routes

VERSION = 'rules-v1'


def route(state: State) -> str:
    allowed = set(allowed_routes(state))
    if allowed == {'finish'}:
        return 'finish'
    goal = state.goal.casefold()
    # 仅根据用户目标判断意图，不将论文标题视为指令。
    compare = bool(re.search(r'比较|对比|compare|contrast|difference', goal))
    links_only = bool(re.search(r'只要链接|只列链接|只找.*链接|只.*书目|links only|only.*links', goal))
    search = bool(re.search(r'找|搜|查找|find|search|look for', goal))
    completed = any(re.search(r'已完成|已解释|已给出|已返回|delivered|completed', r.casefold()) for r in state.results)
    if completed:
        return 'finish'
    if re.search(r'上次说|指定的两篇|相关论文$|哪个方向|同时必须|the other one', goal):
        return 'clarify'
    if re.search(r'我上传|已上传|这篇|this paper', goal) and not state.available_pdfs:
        return 'clarify'
    if re.search(r'这篇|this paper', goal) and len(state.available_pdfs) > 1:
        return 'clarify'
    if compare and not state.available_pdfs and not state.candidates and re.search(r'这两篇|these two', goal):
        return 'clarify'
    if not re.search(r'找|搜|解释|概括|分析|读|比较|对比|什么|如何|为何|是否|find|search|read|explain|what|how|compare|contrast', goal):
        return 'clarify'
    if links_only and state.candidates:
        return 'finish'
    if compare and 'compare' in allowed:
        return 'compare'
    if not compare and not links_only and 'answer' in allowed and not search:
        return 'answer'
    if 'download_pdf' in allowed and not links_only:
        return 'download_pdf'
    if 'search' in allowed:
        return 'search'
    return 'finish'
