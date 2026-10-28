"""Frozen, deliberately simple heuristic. No access to labels or sample IDs.

This is a reference baseline, not an application router. Broad title relevance,
multi-goal completion and ambiguous references are deliberately not solved here.
"""
import re

from .contract import State, allowed_routes

VERSION = 'rules-v1'


def route(state: State) -> str:
    allowed = set(allowed_routes(state))
    if allowed == {'finish'}:
        return 'finish'
    goal = state.goal.casefold()
    # Look only at the user's goal for intent; never treat titles as instructions.
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
