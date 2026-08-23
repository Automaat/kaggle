import dataclasses
import importlib
import pathlib
import sys

import pytest


TOOLS = pathlib.Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))
oracle = importlib.import_module("economics.milp_oracle")
market = importlib.import_module("economics.market_ledger")


def _input(
    day=0,
    cash=3000.0,
    reserve=0.0,
    seeds=None,
    goods=None,
    existing=(),
    tiles=1,
    actions=100,
    storage=100,
    feed=0,
    fertilizer=0,
    fertilizer_supply=0,
    fertilizer_price=100.0,
    slots=5,
    return_actions=0,
    sale_limit=20,
):
    horizon = 30 - day
    return oracle.OracleInput(
        source_step=day * 24,
        terminal_step=718,
        cash=cash,
        cash_reserve=reserve,
        seeds=seeds or (0,) * len(market.CROPS),
        goods=goods or (0,) * len(market.CROPS),
        existing_plants=existing,
        tile_capacity=(tiles,) * horizon,
        action_capacity=(actions,) * horizon,
        crop_storage_capacity=(storage,) * horizon,
        wheat_demand=(feed,) * horizon,
        fixed_cash_flow=(0.0,) * horizon,
        fertilizer_stock=fertilizer,
        fertilizer_supply=(fertilizer_supply,) * horizon,
        fertilizer_buy_price=(fertilizer_price,) * horizon,
        market_order_slots=(slots,) * horizon,
        base_inventory=tuple((10_000,) * len(market.CROPS) for _ in range(horizon)),
        wheat_buy_price=(25.0,) * horizon,
        first_plant_day=day,
        terminal_return_actions=return_actions,
        sale_unit_limit=sale_limit,
        scenario="no-future-opponent-orders-v1",
    )


def _option(options, crop, plant_day, harvest_day):
    return next(
        value
        for value in options
        if value.crop == crop
        and value.plant_day == plant_day
        and value.harvest_day == harvest_day
    )


def test_one_time_crop_options_use_exact_unfertilized_yield():
    options = oracle.generate_crop_options(_input())
    assert _option(options, "WHEAT", 0, 4).yield_units == 4
    assert _option(options, "CARROT", 0, 3).yield_units == 3
    assert _option(options, "MELON", 0, 10).yield_units == 6


def test_ongoing_options_stop_at_four_productions():
    options = oracle.generate_crop_options(_input())
    assert _option(options, "STRAWBERRY", 0, 10).yield_units == 1
    final = _option(options, "STRAWBERRY", 0, 16)
    assert final.yield_units == 4
    assert final.active_days[-1] == 16
    assert final.release_day == 17
    assert final.actions[17] == 1


def test_ongoing_schedule_without_final_harvest_keeps_tile():
    options = oracle.generate_crop_options(_input())
    strawberry = next(
        value
        for value in options
        if value.crop == "STRAWBERRY"
        and value.plant_day == 0
        and value.harvests == ((10, 1),)
        and not value.fertilizer_days
    )
    assert strawberry.release_day is None
    assert strawberry.active_days[-1] == 29


def test_ongoing_cleanup_releases_tile_for_same_day_replanting():
    data = _input(cash=3000, tiles=1, fertilizer_supply=1)
    counts = (0, 0, 0, 1, 0)
    result = oracle.solve_oracle(data, 10, 0, counts)
    strawberry = next(
        value
        for value in result.decisions
        if value.crop == "STRAWBERRY" and value.plant_day == 0
    )
    assert strawberry.release_day == 17
    assert any(
        value.plant_day == strawberry.release_day
        for value in result.decisions
        if value.plant_day is not None
    )
    assert oracle.verify_result(data, result, counts) == ()


def test_ongoing_schedule_harvests_twice_before_held_cap_discards_yield():
    options = oracle.generate_crop_options(_input())
    strawberry = next(
        value
        for value in options
        if value.crop == "STRAWBERRY"
        and value.plant_day == 0
        and value.harvests == ((12, 4), (16, 4))
    )
    assert strawberry.yield_units == 8
    assert strawberry.fertilizer_days == (9, 13)


def test_unfertilized_ongoing_schedule_caps_at_four_total_units():
    options = oracle.generate_crop_options(_input())
    unfertilized = [
        value
        for value in options
        if value.crop == "TOMATO"
        and value.plant_day == 0
        and not value.fertilizer_days
    ]
    assert max(value.yield_units for value in unfertilized) == 4


def test_fertilizer_supply_funds_full_strawberry_schedule():
    data = _input(
        cash=100,
        tiles=1,
        fertilizer_supply=1,
        fertilizer_price=1000,
    )
    counts = (0, 0, 0, 1, 0)
    result = oracle.solve_oracle(data, 10, 0, counts)
    strawberry = next(
        value
        for value in result.decisions
        if value.crop == "STRAWBERRY" and value.plant_day == 0
    )
    assert result.success
    assert strawberry.yield_per_unit == 8
    assert strawberry.fertilizer_days == (9, 13)
    assert not any(value.item == "FERTILIZER" for value in result.purchases)
    assert oracle.verify_result(data, result, counts) == ()


def test_external_cash_flow_can_fund_feed_before_crop_revenue():
    data = dataclasses.replace(
        _input(day=29, cash=0, tiles=0, feed=1, slots=1),
        fixed_cash_flow=(25.0,),
    )
    result = oracle.solve_oracle(data, 10, 0)
    assert result.success
    assert result.terminal_cash == 0
    assert oracle.verify_result(data, result) == ()


def test_late_melon_and_strawberry_have_no_option():
    options = oracle.generate_crop_options(_input(day=20))
    assert not any(
        value.plant_day is not None and value.crop in ("MELON", "STRAWBERRY")
        for value in options
    )


def test_terminal_return_excludes_mature_harvest():
    without_route = oracle.generate_crop_options(_input(day=27, return_actions=0))
    with_route = oracle.generate_crop_options(_input(day=27, return_actions=23))
    assert any(value.crop == "CARROT" for value in without_route)
    assert not any(value.crop == "CARROT" for value in with_route)


def test_one_time_tile_can_turn_over_but_ongoing_tile_stays_occupied():
    options = oracle.generate_crop_options(_input())
    carrot = _option(options, "CARROT", 0, 3)
    strawberry = _option(options, "STRAWBERRY", 0, 10)
    assert carrot.active_days == (0, 1, 2)
    assert strawberry.active_days == tuple(range(30))


def test_one_time_tile_can_be_replanted_on_harvest_day():
    options = oracle.generate_crop_options(_input())
    first = _option(options, "CARROT", 0, 2)
    second = _option(options, "CARROT", 2, 4)
    assert not set(first.active_days) & set(second.active_days)


def test_action_vector_charges_plant_water_harvest_and_return():
    option = _option(
        oracle.generate_crop_options(_input(return_actions=3)),
        "CARROT",
        0,
        3,
    )
    assert option.actions[:4] == (2, 1, 1, 5)


def test_hand_computed_last_cycle_prefers_carrot():
    result = oracle.solve_oracle(_input(day=27), 10, 0)
    assert result.success
    selected = [value for value in result.decisions if value.count]
    assert len(selected) == 1
    assert selected[0].crop == "CARROT"
    assert selected[0].plant_day == 27
    assert selected[0].harvest_day == 29
    assert selected[0].count == 1


def test_fixed_first_day_portfolio_is_exact():
    counts = (0, 0, 0, 1, 0)
    data = _input(tiles=1)
    result = oracle.solve_oracle(data, 10, 0, counts)
    assert result.success
    selected = {
        crop: sum(
            value.count
            for value in result.decisions
            if value.crop == crop and value.plant_day == 0
        )
        for crop in market.CROPS
    }
    assert tuple(selected[crop] for crop in market.CROPS) == counts
    assert oracle.verify_result(data, result, counts) == ()


def test_fixed_first_day_portfolio_can_be_infeasible():
    result = oracle.solve_oracle(_input(tiles=1), 10, 0, (0, 2, 0, 0, 0))
    assert not result.success


@pytest.mark.parametrize(
    "counts,error",
    [
        ((1,), TypeError),
        ((True, 0, 0, 0, 0), TypeError),
        ((-1, 0, 0, 0, 0), ValueError),
    ],
)
def test_fixed_first_day_portfolio_validation(counts, error):
    with pytest.raises(error):
        oracle.solve_oracle(_input(), 10, 0, counts)


def test_cash_reserve_can_block_all_planting():
    result = oracle.solve_oracle(_input(day=27, cash=20, reserve=20), 10, 0)
    assert result.success
    assert result.decisions == ()
    assert result.terminal_cash == 20


def test_existing_crop_needs_no_seed_purchase():
    plant = oracle.ExistingPlant((0, 0), "CARROT", 27, 1, False, 0, -1)
    result = oracle.solve_oracle(
        _input(day=29, cash=0, existing=(plant,), tiles=0),
        10,
        0,
    )
    assert result.success
    assert result.decisions[0].existing_position == (0, 0)
    assert result.purchases == ()
    assert result.terminal_cash > 0


def test_existing_one_time_crop_can_be_harvested_after_growth_window():
    plant = oracle.ExistingPlant((0, 0), "WHEAT", 0, 3, False, 0, -1)
    options = oracle.generate_crop_options(
        _input(day=5, cash=0, existing=(plant,), tiles=0)
    )
    existing = [value for value in options if value.existing_index == 0]
    assert len(existing) == 1
    assert existing[0].harvest_day == 5
    assert existing[0].yield_units == 3


def test_existing_one_time_harvest_releases_tile_for_replanting():
    plant = oracle.ExistingPlant((0, 0), "CARROT", 0, 1, False, 0, -1)
    seeds = (0, 1, 0, 0, 0)
    result = oracle.solve_oracle(
        _input(cash=0, seeds=seeds, existing=(plant,), tiles=0),
        10,
        0,
    )
    assert result.success
    assert any(value.existing_position == (0, 0) for value in result.decisions)
    assert any(value.plant_day is not None for value in result.decisions)


def test_zero_action_capacity_blocks_new_crops():
    result = oracle.solve_oracle(_input(day=27, actions=0), 10, 0)
    assert result.success
    assert result.decisions == ()


def test_feed_balance_buys_wheat():
    result = oracle.solve_oracle(
        _input(day=29, cash=25, tiles=0, feed=1, slots=1),
        10,
        0,
    )
    assert result.success
    assert result.purchases == (oracle.PurchaseDecision("WHEAT", 29, 1),)
    assert result.terminal_unsold_goods[0] == 0


def test_feed_bound_covers_demand_without_crop_tiles():
    result = oracle.solve_oracle(
        _input(day=29, cash=250, tiles=0, feed=10, slots=1),
        10,
        0,
    )
    assert result.success
    assert result.purchases == (oracle.PurchaseDecision("WHEAT", 29, 10),)


def test_feed_without_cash_is_infeasible():
    result = oracle.solve_oracle(
        _input(day=29, cash=0, tiles=0, feed=1, slots=1),
        10,
        0,
    )
    assert not result.success
    assert result.terminal_cash is None


def test_unsold_goods_have_zero_terminal_value():
    goods = (0, 2, 0, 0, 0)
    result = oracle.solve_oracle(
        _input(day=29, cash=0, goods=goods, tiles=0, slots=0),
        10,
        0,
    )
    assert result.success
    assert result.terminal_cash == 0
    assert result.terminal_unsold_goods[1] == 2


def test_crop_storage_capacity_blocks_excess_inventory():
    goods = (100, 0, 0, 0, 0)
    plant = oracle.ExistingPlant((0, 0), "CARROT", 27, 2, False, 0, -1)
    result = oracle.solve_oracle(
        _input(
            day=29,
            cash=0,
            goods=goods,
            existing=(plant,),
            tiles=0,
            slots=1,
            storage=100,
            sale_limit=1,
        ),
        10,
        0,
    )
    assert result.success
    assert result.decisions == ()
    assert sum(result.terminal_unsold_goods) == 99


def test_marginal_market_quotes_are_nonincreasing():
    values = oracle._marginal_prices(_input())
    for prices in values.values():
        assert all(left >= right for left, right in zip(prices, prices[1:]))


def test_input_hash_is_deterministic_and_sensitive():
    first = _input()
    second = dataclasses.replace(first, cash=first.cash + 1)
    assert oracle.input_sha256(first) == oracle.input_sha256(first)
    assert oracle.input_sha256(first) != oracle.input_sha256(second)


def test_result_verifier_accepts_solver_plan_and_rejects_changed_balance():
    data = _input(day=27)
    result = oracle.solve_oracle(data, 10, 0)
    assert oracle.verify_result(data, result) == ()
    changed = dataclasses.replace(
        result,
        terminal_cash=result.terminal_cash + 1,
    )
    assert "terminal cash mismatch" in oracle.verify_result(data, changed)


def test_input_rejects_invalid_horizon():
    with pytest.raises(TypeError):
        dataclasses.replace(_input(), tile_capacity=(1,))
