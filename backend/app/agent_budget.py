from contextvars import ContextVar
from dataclasses import dataclass


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Budget:
    generations: int = 0
    embeddings: int = 0
    input_chars: int = 0

    def reserve(self, kind, chars):
        limit = 12 if kind == 'generations' else 80
        if getattr(self, kind) >= limit:
            label = '生成模型' if kind == 'generations' else 'Embedding'
            raise BudgetExceeded(f'已达到本次任务的 {label} 请求上限（{limit} 次），保留已有结果。')
        if self.input_chars + chars > 300_000:
            raise BudgetExceeded(f'下一次请求将超过本次任务的 300000 输入字符上限（已使用 {self.input_chars} 字符）；并非服务商额度耗尽，保留已有结果。')
        setattr(self, kind, getattr(self, kind) + 1)
        self.input_chars += chars


current_budget: ContextVar[Budget | None] = ContextVar('agent_budget', default=None)


def reserve(kind, chars):
    budget = current_budget.get()
    if budget is not None:
        budget.reserve(kind, chars)
