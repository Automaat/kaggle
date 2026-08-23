from dataclasses import replace

import pytest

from kaggriculture.tools.economics import animal_milp as oracle


def _input(
    source_step=576,
    terminal_step=718,
    goods=(0, 0, 0, 0, 0),
    shed_animals=(0, 0, 0),
    existing_animals=(),
    empty_structures=(0, 0),
    max_new_animals=0,
    allowed_animals=oracle.ANIMALS,
    fixed_slot_animals=(),
):
    days = terminal_step // 24 - source_step // 24 + 1
    initial_tiles = len(existing_animals) + sum(empty_structures)
    return oracle.AnimalOracleInput(
        source_step=source_step,
        terminal_step=terminal_step,
        cash=3000.0,
        cash_reserve=400.0,
        goods=goods,
        shed_animals=shed_animals,
        existing_animals=existing_animals,
        empty_structures=empty_structures,
        animal_tile_capacity=(max(4, initial_tiles),) * days,
        action_capacity=(20,) * days,
        shed_capacity=(100,) * days,
        fixed_shed_occupancy=(0,) * days,
        market_order_slots=(10,) * days,
        fixed_cash_flow=(0.0,) * days,
        base_inventory=tuple((10_000,) * len(oracle.GOODS) for _ in range(days)),
        wheat_buy_unit_limit=40,
        placement_travel_actions=1,
        feed_actions_per_unit=2,
        return_actions=1,
        sale_unit_limit=12,
        max_new_animals=max_new_animals,
        allowed_animals=tuple(allowed_animals),
        fixed_slot_animals=tuple(fixed_slot_animals),
        scenario="no-future-opponent-orders-v1",
    )


def _existing(
    animal="GOOSE",
    placed_day=20,
    yield_units=0,
    consecutive_unfed=0,
    fed_today=False,
    cared_today=False,
    fertilizer_available=False,
    pending_care_bonus=0,
):
    return oracle.ExistingAnimal(
        "a0",
        (0, 0),
        animal,
        placed_day,
        yield_units,
        consecutive_unfed,
        fed_today,
        cared_today,
        fertilizer_available,
        pending_care_bonus,
    )


def _solve(data):
    result = oracle.solve_animal_oracle(data, 20, 0)
    assert result.success, result.message
    assert result.mip_gap == 0
    assert oracle.verify_result(data, result) == ()
    return result


@pytest.mark.parametrize(
    ("animal", "placed_day", "due_days"),
    (
        ("GOOSE", 0, (3, 4, 5)),
        ("COW", 0, (7, 9, 11)),
        ("SHEEP", 0, (5, 8, 11)),
    ),
)
def test_production_dates_match_animal_ledger(animal, placed_day, due_days):
    observed = tuple(
        day
        for day in range(15)
        if oracle._production_due(animal, placed_day, day)
    )
    assert observed[:3] == due_days


def test_existing_care_bank_pays_on_fed_production():
    animal = _existing(
        "COW",
        placed_day=17,
        fed_today=True,
        cared_today=True,
        pending_care_bonus=2,
    )
    result = _solve(_input(existing_animals=(animal,)))
    service = next(value for value in result.services if value.day == 24)
    assert service.production == 3
    assert service.pending_care_end == 1


def test_unfed_production_wipes_care_bank():
    animal = _existing("COW", placed_day=17, pending_care_bonus=2)
    data = _input(existing_animals=(animal,))
    data = replace(data, action_capacity=(0,) + data.action_capacity[1:])
    result = _solve(data)
    service = next(value for value in result.services if value.day == 24)
    assert service.production == 0
    assert service.pending_care_end == 0


def test_held_product_cap_records_overflow():
    animal = _existing(
        "GOOSE",
        placed_day=21,
        yield_units=4,
        fed_today=True,
        pending_care_bonus=1,
    )
    data = _input(existing_animals=(animal,))
    data = replace(data, action_capacity=(0,) + data.action_capacity[1:])
    result = _solve(data)
    service = next(value for value in result.services if value.day == 24)
    assert service.production == 2
    assert service.overflow == 2
    assert service.held_end == 4


def test_terminal_harvest_empties_existing_animal():
    animal = _existing("GOOSE", placed_day=20, yield_units=4)
    result = _solve(_input(source_step=696, existing_animals=(animal,)))
    service = result.services[0]
    assert service.harvest_action
    assert service.harvested == 4
    assert service.held_end == 0
    assert sum(value.quantity for value in result.sales if value.item == "EGG") == 4


def test_unreachable_terminal_harvest_keeps_product_unsold():
    animal = _existing("GOOSE", placed_day=20, yield_units=4)
    data = _input(source_step=718, existing_animals=(animal,))
    result = _solve(data)
    service = result.services[0]
    assert not service.harvest_action
    assert service.held_end == 4
    assert result.sales == ()


def test_final_day_existing_service_flags_do_not_require_refresh():
    animal = _existing(fed_today=True, cared_today=True)
    result = _solve(_input(source_step=696, existing_animals=(animal,)))
    service = result.services[0]
    assert not service.feed_action
    assert not service.care_action
    assert service.pending_care_end == 0


def test_last_hour_harvest_uses_end_of_day_drop():
    animal = _existing("GOOSE", placed_day=20, yield_units=4, fed_today=True)
    result = _solve(_input(source_step=695, existing_animals=(animal,)))
    service = next(value for value in result.services if value.day == 28)
    assert service.harvest_action
    assert service.harvest_deferred
    assert not any(value.day == 28 and value.item == "EGG" for value in result.sales)
    assert any(value.day == 29 and value.item == "EGG" for value in result.sales)


def test_existing_fertilizer_can_be_collected_and_sold():
    animal = _existing(fertilizer_available=True)
    result = _solve(_input(source_step=696, existing_animals=(animal,)))
    assert result.services[0].fertilizer_collected == 1
    assert any(value.item == "FERTILIZER" for value in result.sales)


def test_one_missed_feed_forces_current_feed():
    animal = _existing(consecutive_unfed=1)
    result = _solve(_input(goods=(1, 0, 0, 0, 0), existing_animals=(animal,)))
    service = next(value for value in result.services if value.day == 24)
    assert service.feed_action


def test_wheat_bought_today_can_feed_after_next_observation():
    animal = _existing(consecutive_unfed=1)
    data = _input(existing_animals=(animal,))
    result = _solve(data)
    service = next(value for value in result.services if value.day == 24)
    purchase = next(value for value in result.purchases if value.item == "WHEAT")
    assert service.feed_action
    assert purchase.day == 24
    assert purchase.cost == 26


def test_last_hour_wheat_purchase_cannot_feed_before_refresh():
    animal = _existing(placed_day=20, consecutive_unfed=1)
    data = _input(
        source_step=695,
        goods=(1, 0, 0, 0, 0),
        existing_animals=(animal,),
    )
    result = oracle.solve_animal_oracle(data, 20, 0)
    assert not result.success


def test_terminal_step_stops_later_days_and_refreshes():
    animal = _existing(placed_day=20, consecutive_unfed=1)
    data = _input(
        source_step=576,
        terminal_step=576,
        goods=(1, 0, 0, 0, 0),
        existing_animals=(animal,),
    )
    result = _solve(data)
    assert tuple(value.day for value in result.balances) == (24,)
    assert tuple(value.day for value in result.services) == (24,)
    assert result.services[0].production == 0
    assert not result.services[0].feed_action


def test_free_shed_animal_uses_structure_without_purchase():
    data = _input(
        source_step=552,
        shed_animals=(1, 0, 0),
        empty_structures=(1, 0),
        max_new_animals=1,
        allowed_animals=("GOOSE",),
        fixed_slot_animals=("GOOSE",),
    )
    result = _solve(data)
    assert len(result.animals) == 1
    assert not any(value.item == "GOOSE" for value in result.purchases)
    assert result.structures == ()


def test_new_animal_purchase_precedes_placement():
    data = _input(
        source_step=0,
        max_new_animals=1,
        allowed_animals=("GOOSE",),
        fixed_slot_animals=("GOOSE",),
    )
    result = _solve(data)
    decision = result.animals[0]
    purchase_day = next(value.day for value in result.purchases if value.item == "GOOSE")
    assert purchase_day <= decision.placement_day
    assert any(value.structure == "COOP" for value in result.structures)


def test_cash_reserve_blocks_animal_purchase():
    data = _input(
        source_step=0,
        max_new_animals=1,
        allowed_animals=("SHEEP",),
        fixed_slot_animals=("SHEEP",),
    )
    data = replace(data, cash=500.0, cash_reserve=500.0)
    result = _solve(data)
    assert result.animals == ()


def test_zero_market_slots_prevent_terminal_sale():
    animal = _existing("GOOSE", yield_units=4)
    data = _input(source_step=696, existing_animals=(animal,))
    data = replace(data, market_order_slots=(0,))
    result = _solve(data)
    assert result.sales == ()
    assert result.services[0].held_end == 4


def test_input_and_result_are_deterministic():
    data = _input(source_step=696, goods=(0, 2, 0, 0, 0))
    first = _solve(data)
    second = _solve(data)
    assert oracle.input_sha256(data) == oracle.input_sha256(data)
    assert replace(first, wall_seconds=0) == replace(second, wall_seconds=0)


def test_rejects_duplicate_existing_positions():
    first = _existing()
    second = replace(first, identifier="a1")
    with pytest.raises(ValueError, match="positions"):
        _input(existing_animals=(first, second))


def test_rejects_fixed_slots_outside_allowed_animals():
    with pytest.raises(ValueError, match="not allowed"):
        _input(
            max_new_animals=1,
            allowed_animals=("GOOSE",),
            fixed_slot_animals=("COW",),
        )


def test_rejects_negative_pending_care():
    with pytest.raises(ValueError, match="nonnegative"):
        _existing(pending_care_bonus=-1)


def test_default_terminal_boundary_preserves_legacy_result_contract():
    data = _input(source_step=576, terminal_step=647)
    result = _solve(data)
    assert data.last_day == 26
    assert data.horizon_days == 3
    assert result.terminal_value is None
    assert result.forecast_terminal_cash is None


@pytest.mark.parametrize("source_step", (576, 600))
def test_three_day_terminal_value_starts_animal_without_procrastination(source_step):
    data = _input(
        source_step=source_step,
        terminal_step=source_step + 71,
        max_new_animals=1,
        allowed_animals=("GOOSE",),
        fixed_slot_animals=("GOOSE",),
    )
    baseline = _solve(data)
    terminal_values = oracle.AnimalTerminalValues(
        active_animals=(500.0, 0.0, 0.0),
        goods=(100.0, 10.0, 0.0, 0.0, 0.0),
        shed_animals=(250.0, 0.0, 0.0),
        empty_structures=(20.0, 0.0),
    )
    valued_data = replace(data, terminal_values=terminal_values)
    valued = oracle.solve_animal_oracle(valued_data, 20, 0)
    assert valued.success, valued.message
    assert oracle.verify_result(valued_data, valued) == ()
    assert baseline.animals == ()
    assert tuple(value.animal for value in valued.animals) == ("GOOSE",)
    assert valued.animals[0].placement_day == source_step // 24
    assert valued.terminal_cash < baseline.terminal_cash
    assert valued.forecast_terminal_cash > baseline.terminal_cash


def test_terminal_value_preserves_wheat_feed_stock():
    data = _input(
        source_step=576,
        terminal_step=647,
        goods=(3, 0, 0, 0, 0),
    )
    terminal_values = oracle.AnimalTerminalValues(
        active_animals=(0.0, 0.0, 0.0),
        goods=(100.0, 0.0, 0.0, 0.0, 0.0),
        shed_animals=(0.0, 0.0, 0.0),
        empty_structures=(0.0, 0.0),
    )
    baseline = _solve(data)
    valued = _solve(replace(data, terminal_values=terminal_values))
    assert baseline.terminal_goods[0] == 0
    assert valued.terminal_goods[0] == 3


def test_terminal_value_reports_all_animal_boundary_assets():
    animal = _existing("GOOSE", placed_day=20, yield_units=4)
    data = _input(
        source_step=718,
        terminal_step=718,
        goods=(3, 0, 0, 0, 0),
        shed_animals=(1, 0, 0),
        existing_animals=(animal,),
        empty_structures=(1, 0),
    )
    terminal_values = oracle.AnimalTerminalValues(
        active_animals=(100.0, 0.0, 0.0),
        goods=(5.0, 2.0, 0.0, 0.0, 0.0),
        shed_animals=(50.0, 0.0, 0.0),
        empty_structures=(20.0, 0.0),
    )
    data = replace(
        data,
        market_order_slots=(0,),
        terminal_values=terminal_values,
    )
    result = _solve(data)
    assert result.terminal_cash == 3000.0
    assert result.terminal_value == 193.0
    assert result.forecast_terminal_cash == 3193.0


def test_rejects_negative_terminal_value():
    with pytest.raises(ValueError, match="nonnegative"):
        oracle.AnimalTerminalValues(
            active_animals=(-1.0, 0.0, 0.0),
            goods=(0.0, 0.0, 0.0, 0.0, 0.0),
            shed_animals=(0.0, 0.0, 0.0),
            empty_structures=(0.0, 0.0),
        )
