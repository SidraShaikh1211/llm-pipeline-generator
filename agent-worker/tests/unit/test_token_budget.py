from __future__ import annotations

import pytest

from agent_worker.agent.token_budget import TokenBudget, TokenBudgetExceeded


def test_budget_check_passes_under_limit() -> None:
    b = TokenBudget(max_total=100)
    b.add(50)
    b.check()
    assert b.remaining == 50


def test_budget_check_raises_at_limit() -> None:
    b = TokenBudget(max_total=100)
    b.add(100)
    with pytest.raises(TokenBudgetExceeded):
        b.check()


def test_budget_check_raises_over_limit() -> None:
    b = TokenBudget(max_total=100)
    b.add(150)
    with pytest.raises(TokenBudgetExceeded):
        b.check()


def test_budget_remaining_is_floored_at_zero() -> None:
    b = TokenBudget(max_total=10)
    b.add(50)
    assert b.remaining == 0


def test_budget_rejects_negative_add() -> None:
    b = TokenBudget(max_total=100)
    with pytest.raises(ValueError, match="negative"):
        b.add(-5)


def test_budget_rejects_non_positive_max() -> None:
    with pytest.raises(ValueError, match="positive"):
        TokenBudget(max_total=0)
