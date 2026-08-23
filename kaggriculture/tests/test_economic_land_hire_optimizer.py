import copy
import dataclasses
import importlib
import pathlib
import sys
import types

import pytest


TOOLS = pathlib.Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))
optimizer = importlib.import_module("economics.land_hire_optimizer")
market = importlib.import_module("economics.market_ledger")
runner = importlib.import_module("economics.run_land_hire_optimizer")


def _values(value, length):
    if type(value) is tuple:
        return value
    return (value,) * length


def _input(
    source=0,
    terminal=3,
    cash=10_000.0,
    reserve=0.0,
    unlocked=1,
    hands=0,
    max_hands=8,
    multiplier=1,
    cash_flow=0.0,
    slots=10,
    existing_work=0,
    land_work=0,
    base_capacity=0,
    executor_capacity=8,
    work_value=0.0,
):
    horizon = terminal - source + 1
    return optimizer.OptimizerInput(
        source_step=source,
        terminal_step=terminal,
        cash=cash,
        cash_reserve=reserve,
        unlocked_quadrants=unlocked,
        hands_today=hands,
        hires_today=hands,
        max_hands_per_day=max_hands,
        hire_multiplier=multiplier,
        fixed_cash_flow=_values(cash_flow, horizon),
        market_order_slots=_values(slots, horizon),
        existing_work=_values(existing_work, horizon),
        land_work_per_quadrant=_values(land_work, horizon),
        base_work_capacity=_values(base_capacity, horizon),
        executor_work_capacity=_values(executor_capacity, horizon),
        terminal_value_per_work=_values(work_value, horizon),
        scenario="unit-test-v1",
    )


def _solve(data, mode):
    result = optimizer.solve_optimizer(data, mode, 10, 0)
    assert result.success
    assert optimizer.verify_result(data, result) == ()
    return result


def _account(money, hires, unlocked=1):
    return market.PlayerAccount(
        money,
        (0,) * len(market.SHED_ITEMS),
        (0,) * len(market.CROPS),
        hires,
        unlocked,
        hires,
    )


def test_land_uses_all_three_sequential_prices():
    data = _input(
        terminal=1,
        max_hands=80,
        land_work=25,
        base_capacity=75,
        executor_capacity=75,
        work_value=(0.0, 300.0),
    )
    result = _solve(data, "land-only")
    decisions = result.investments
    assert tuple(value.cost for value in decisions) == (1000.0, 2000.0, 4000.0)
    assert tuple(value.marginal_index for value in decisions) == (1, 2, 3)
    assert tuple(value.order_index for value in decisions) == (0, 1, 2)


def test_land_adds_exactly_25_tiles_after_purchase_observation():
    data = _input(
        terminal=1,
        max_hands=30,
        land_work=25,
        base_capacity=25,
        executor_capacity=25,
        work_value=(0.0, 100.0),
    )
    result = _solve(data, "land-only")
    assert len(result.investments) == 1
    assert result.investments[0].available_from_step == 1
    assert result.projections[0].land_work == 0
    assert result.projections[1].land_work == optimizer.TILES_PER_QUADRANT
    assert result.projections[1].unlocked_quadrants == 2


def test_terminal_land_has_no_payback_and_is_not_bought():
    result = _solve(
        _input(
            source=718,
            terminal=718,
            max_hands=30,
            land_work=25,
            base_capacity=25,
            executor_capacity=25,
            work_value=10_000.0,
        ),
        "land-only",
    )
    assert result.investments == ()
    assert result.incremental_terminal_cash == 0


def test_land_respects_order_slots_and_cash_reserve():
    blocked_slots = _solve(
        _input(
            terminal=1,
            slots=0,
            max_hands=30,
            land_work=25,
            base_capacity=25,
            executor_capacity=25,
            work_value=(0.0, 100.0),
        ),
        "land-only",
    )
    blocked_cash = _solve(
        _input(
            terminal=1,
            cash=1500,
            reserve=1000,
            max_hands=30,
            land_work=25,
            base_capacity=25,
            executor_capacity=25,
            work_value=(0.0, 100.0),
        ),
        "land-only",
    )
    assert blocked_slots.investments == ()
    assert blocked_cash.investments == ()


def test_hire_fibonacci_cost_matches_accepted_market_ledger():
    other = _account(1_000_000, 0)
    for hires_today in range(12):
        account = _account(1_000_000, hires_today)
        state = market.MarketState(
            0,
            (0,) * len(market.PRODUCTS),
            (account, other),
            (),
            config=market.MarketConfig(hire_multiplier=3),
        )
        result = market.apply_market_phase(state, ([["HIRE"]], []))
        accepted = result.after_town.players[0]
        expected = optimizer.hire_cost(hires_today, 3)
        assert account.money - accepted.money == expected


@pytest.mark.parametrize(
    ("source", "expected_steps", "expected_work"),
    ((0, 23, 23), (22, 1, 1), (23, 0, 0)),
)
def test_hire_uses_only_remaining_day_capacity(source, expected_steps, expected_work):
    result = _solve(
        _input(
            source=source,
            terminal=23,
            max_hands=1,
            existing_work=1,
            executor_capacity=1,
            work_value=10.0,
        ),
        "hire-only",
    )
    if expected_steps == 0:
        assert result.investments == ()
    else:
        assert len(result.investments) == 1
        assert result.investments[0].available_source_steps == expected_steps
    assert sum(value.completed_work for value in result.projections) == expected_work


def test_hire_uses_current_hires_today_for_cost():
    data = _input(
        source=10,
        terminal=11,
        hands=3,
        max_hands=4,
        existing_work=4,
        base_capacity=3,
        executor_capacity=4,
        work_value=(0.0, 10.0),
    )
    result = _solve(data, "hire-only")
    assert len(result.investments) == 1
    assert result.investments[0].marginal_index == 3
    assert result.investments[0].cost == 3


def test_hires_reset_before_next_day():
    data = _input(
        source=22,
        terminal=24,
        max_hands=1,
        existing_work=(0, 1, 1),
        executor_capacity=1,
        work_value=(0.0, 10.0, 10.0),
    )
    result = _solve(data, "hire-only")
    assert result.projections[1].hands == 1
    assert result.projections[2].hands == 0
    assert result.investments[0].available_from_step == 23


def test_hire_is_unavailable_on_purchase_step():
    data = _input(
        terminal=1,
        max_hands=1,
        existing_work=(1, 0),
        executor_capacity=1,
        work_value=(100.0, 0.0),
    )
    result = _solve(data, "hire-only")
    assert result.investments == ()
    assert result.projections[0].completed_work == 0


def test_hire_without_useful_work_has_no_payback():
    result = _solve(
        _input(
            terminal=23,
            max_hands=8,
            existing_work=0,
            executor_capacity=8,
            work_value=1000.0,
        ),
        "hire-only",
    )
    assert result.investments == ()


def test_hire_respects_hand_limit_slots_and_reserve():
    hand_limit = _solve(
        _input(
            terminal=2,
            hands=1,
            max_hands=1,
            existing_work=2,
            base_capacity=1,
            executor_capacity=2,
            work_value=100.0,
        ),
        "hire-only",
    )
    slots = _solve(
        _input(
            terminal=2,
            max_hands=1,
            slots=0,
            existing_work=1,
            executor_capacity=1,
            work_value=100.0,
        ),
        "hire-only",
    )
    reserve = _solve(
        _input(
            terminal=2,
            cash=1,
            reserve=1,
            max_hands=1,
            existing_work=1,
            executor_capacity=1,
            work_value=100.0,
        ),
        "hire-only",
    )
    assert hand_limit.investments == ()
    assert slots.investments == ()
    assert reserve.investments == ()


def test_combined_capacity_interaction_beats_independent_arms():
    data = _input(
        terminal=1,
        max_hands=1,
        slots=(2, 0),
        land_work=1,
        executor_capacity=1,
        work_value=(0.0, 2000.0),
    )
    land = _solve(data, "land-only")
    hire = _solve(data, "hire-only")
    combined = _solve(data, "combined")
    assert land.investments == ()
    assert hire.investments == ()
    assert tuple(value.operation for value in combined.investments) == (
        "BUY_LAND",
        "HIRE",
    )
    assert tuple(value.order_index for value in combined.investments) == (0, 1)
    assert combined.projections[0].completed_work == 0
    assert combined.projections[1].completed_work == 1
    assert combined.incremental_terminal_cash == 999


def test_combined_investments_share_market_slots():
    data = _input(
        terminal=1,
        max_hands=1,
        slots=(1, 0),
        land_work=1,
        existing_work=1,
        executor_capacity=1,
        work_value=(0.0, 2000.0),
    )
    result = _solve(data, "combined")
    assert len(result.investments) == 1


def test_executor_cap_blocks_theoretical_hire_capacity():
    data = _input(
        terminal=2,
        max_hands=8,
        existing_work=8,
        executor_capacity=1,
        work_value=100.0,
    )
    result = _solve(data, "hire-only")
    assert len(result.investments) == 1
    assert all(value.completed_work <= 1 for value in result.projections)


def test_result_verifier_rejects_changed_projection():
    data = _input(
        terminal=1,
        max_hands=1,
        existing_work=1,
        executor_capacity=1,
        work_value=10.0,
    )
    result = _solve(data, "hire-only")
    changed_projection = dataclasses.replace(
        result.projections[-1],
        completed_work=result.projections[-1].completed_work + 1,
    )
    changed = dataclasses.replace(
        result,
        projections=result.projections[:-1] + (changed_projection,),
    )
    assert "work opportunities exceeded" in optimizer.verify_result(data, changed)


def test_input_hash_is_deterministic_and_sensitive():
    data = _input()
    changed = dataclasses.replace(data, cash=data.cash + 1)
    assert optimizer.input_sha256(data) == optimizer.input_sha256(data)
    assert optimizer.input_sha256(data) != optimizer.input_sha256(changed)


def test_negative_fixed_cash_path_is_infeasible():
    data = _input(cash=10, reserve=5, cash_flow=-6)
    result = optimizer.solve_optimizer(data, "baseline", 10, 0)
    assert not result.success
    assert result.forecast_terminal_cash is None


def test_time_limited_solver_has_no_fabricated_plan(monkeypatch):
    response = types.SimpleNamespace(
        success=False,
        x=None,
        status=1,
        message="Time limit reached",
        mip_gap=None,
    )
    monkeypatch.setattr(optimizer, "milp", lambda *args, **kwargs: response)
    result = optimizer.solve_optimizer(_input(), "combined", 0.001, 0)
    assert not result.success
    assert result.investments == ()
    assert result.projections == ()


def test_input_rejects_land_work_above_25():
    with pytest.raises(ValueError):
        _input(land_work=26)


def test_registered_run_keeps_independent_arms_before_combination():
    document = runner.run(10, 0)
    assert tuple(arm["mode"] for arm in document["arms"]) == runner.MODES
    assert all(arm["success"] for arm in document["arms"])
    assert not any(document["verification_errors"].values())
    baseline, land, hire, combined = document["arms"]
    assert baseline["investments"] == ()
    assert all(value["operation"] == "BUY_LAND" for value in land["investments"])
    assert all(value["operation"] == "HIRE" for value in hire["investments"])
    assert land["incremental_terminal_cash"] > 0
    assert hire["incremental_terminal_cash"] > 0
    assert combined["incremental_terminal_cash"] > max(
        land["incremental_terminal_cash"],
        hire["incremental_terminal_cash"],
    )
    assert document["shadow_only"]
    assert document["realized_simulator_score"] is None
    assert document["score_claim"] is None


def test_registered_run_is_deterministic_without_wall_time():
    first = copy.deepcopy(runner.run(10, 0))
    second = copy.deepcopy(runner.run(10, 0))
    for document in (first, second):
        for arm in document["arms"]:
            arm.pop("wall_seconds")
    assert first == second
