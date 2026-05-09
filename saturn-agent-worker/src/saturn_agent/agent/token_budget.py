from __future__ import annotations


class TokenBudgetExceeded(Exception):
    def __init__(self, used: int, max_total: int) -> None:
        super().__init__(f"token budget exceeded: used {used} of {max_total}")
        self.used = used
        self.max_total = max_total


class TokenBudget:
    def __init__(self, max_total: int) -> None:
        if max_total <= 0:
            raise ValueError(f"max_total must be positive, got {max_total}")
        self.max_total = max_total
        self.used = 0

    def check(self) -> None:
        if self.used >= self.max_total:
            raise TokenBudgetExceeded(self.used, self.max_total)

    def add(self, n: int) -> None:
        if n < 0:
            raise ValueError(f"cannot add negative tokens: {n}")
        self.used += n

    @property
    def remaining(self) -> int:
        return max(0, self.max_total - self.used)
