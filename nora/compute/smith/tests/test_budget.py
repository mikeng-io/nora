import pytest

from budget import BudgetExceededError, ToolCallBudget, guarded


def test_a_fresh_budget_allows_calls_up_to_its_limit():
    budget = ToolCallBudget(max_calls=3)
    budget.spend()
    budget.spend()
    budget.spend()
    assert budget.remaining == 0


def test_spending_past_the_limit_raises():
    budget = ToolCallBudget(max_calls=1)
    budget.spend()
    with pytest.raises(BudgetExceededError):
        budget.spend()


def test_guarded_wraps_a_function_and_spends_the_budget():
    budget = ToolCallBudget(max_calls=2)
    calls = []

    @guarded(budget)
    def do_thing(x: int) -> int:
        calls.append(x)
        return x * 2

    assert do_thing(1) == 2
    assert do_thing(2) == 4
    assert calls == [1, 2]
    with pytest.raises(BudgetExceededError):
        do_thing(3)
    assert calls == [1, 2]  # the third call never ran its body
