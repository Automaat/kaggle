import dataclasses
import importlib
import pathlib
import sys

import pytest


TOOLS = pathlib.Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))
ledger = importlib.import_module("economics.inventory_ledger")
market = importlib.import_module("economics.market_ledger")
validator = importlib.import_module("economics.validate_inventory_ledger")


def _shed(**values):
    result = {item: 0 for item in market.SHED_ITEMS}
    result.update(values)
    return result


def _account(hands=0, shed=None, **values):
    return market.PlayerAccount.from_mappings(
        values.get("money", 3000.0),
        shed or _shed(),
        {item: 0 for item in market.CROPS},
        values.get("hires_today", 0),
        1,
        hands,
    )


def _state(
    first=None,
    second=None,
    first_units=None,
    second_units=None,
    step=1,
    capacity=100,
):
    accounts = (first or _account(), second or _account())
    market_state = market.MarketState.from_mappings(
        step,
        {
            item: int(param.I0)
            for item, param in zip(market.PRODUCTS, market.DEFAULT_MARKET_PARAMS)
        },
        accounts,
        (),
        market.DEFAULT_MARKET_PARAMS,
        market.MarketConfig(shed_capacity=capacity),
    )
    units = (
        first_units
        or tuple(ledger.UnitInventory() for _ in range(accounts[0].hands + 1)),
        second_units
        or tuple(ledger.UnitInventory() for _ in range(accounts[1].hands + 1)),
    )
    return ledger.InventoryState(market_state, units)


def _pass_actions(first_hands=None, second_hands=None):
    return (
        (["PASS"], [] if first_hands is None else first_hands),
        (["PASS"], [] if second_hands is None else second_hands),
    )


def test_unit_inventory_preserves_delete_and_readd_order():
    inventory = ledger.UnitInventory((("WHEAT", 1), ("CARROT", 2)))
    inventory, accepted = inventory.take("WHEAT", 1)
    inventory = inventory.add("WHEAT", 3)
    assert accepted == 1
    assert inventory.entries == (("CARROT", 2), ("WHEAT", 3))


@pytest.mark.parametrize(
    "entries,error",
    (
        ((("UNKNOWN", 1),), ValueError),
        ((("WHEAT", 0),), ValueError),
        ((("WHEAT", True),), TypeError),
        ((("WHEAT", 1), ("WHEAT", 2)), ValueError),
        ((["WHEAT", 1],), TypeError),
    ),
)
def test_unit_inventory_rejects_invalid_entries(entries, error):
    with pytest.raises(error):
        ledger.UnitInventory(entries)


def test_fixed_vector_transfer_obeys_capacity():
    source = tuple(5 if item == "WHEAT" else 0 for item in market.SHED_ITEMS)
    destination = tuple(2 if item == "CARROT" else 0 for item in market.SHED_ITEMS)
    source, destination, accepted = ledger.transfer(
        source,
        destination,
        "WHEAT",
        5,
        4,
    )
    assert accepted == 2
    assert source[market.SHED_ITEMS.index("WHEAT")] == 3
    assert destination[market.SHED_ITEMS.index("WHEAT")] == 2


def test_pickup_then_market_sell_sees_reduced_shed():
    model_state = _state(first=_account(shed=_shed(WHEAT=2)))
    result = ledger.apply_inventory_phase(
        model_state,
        ((["PICKUP", "WHEAT", 1], []), (["PASS"], [])),
        ((True,), (True,)),
        ([["SELL", "WHEAT", 2]], []),
    )
    assert result.after_units.market.players[0].shed[0] == 1
    assert result.after_town.market.players[0].shed[0] == 0
    assert result.after_town.units[0][0].entries == (("WHEAT", 1),)


def test_drop_then_market_sell_sees_deposit():
    model_state = _state(
        first_units=(ledger.UnitInventory((("WHEAT", 2),)),),
    )
    result = ledger.apply_inventory_phase(
        model_state,
        ((["DROP"], []), (["PASS"], [])),
        ((True,), (True,)),
        ([["SELL", "WHEAT", 2]], []),
    )
    assert result.after_units.market.players[0].shed[0] == 2
    assert result.after_town.market.players[0].shed[0] == 0
    assert result.after_town.units[0][0] == ledger.UnitInventory()


def test_place_retains_overflow_but_drop_discards_it():
    model_state = _state(
        first=_account(shed=_shed(CARROT=1)),
        first_units=(ledger.UnitInventory((("WHEAT", 3),)),),
        capacity=2,
    )
    placed = ledger.apply_inventory_phase(
        model_state,
        ((["PLACE", "WHEAT", 3], []), (["PASS"], [])),
        ((True,), (True,)),
        ([], []),
    )
    dropped = ledger.apply_inventory_phase(
        model_state,
        ((["DROP"], []), (["PASS"], [])),
        ((True,), (True,)),
        ([], []),
        trace=True,
    )
    assert placed.after_town.units[0][0].entries == (("WHEAT", 2),)
    assert dropped.after_town.units[0][0] == ledger.UnitInventory()
    assert dropped.events[0].discarded == 2


def test_hire_appends_inventory_after_unit_phase():
    model_state = _state()
    result = ledger.apply_inventory_phase(
        model_state,
        _pass_actions(),
        ((True,), (True,)),
        ([["HIRE"]], []),
    )
    assert len(result.after_units.units[0]) == 1
    assert len(result.after_town.units[0]) == 2
    assert result.after_town.units[0][1] == ledger.UnitInventory()


def test_purchase_can_be_picked_up_on_next_invocation():
    first = ledger.apply_inventory_phase(
        _state(),
        _pass_actions(),
        ((True,), (True,)),
        ([["BUY_PRODUCT", "WHEAT", 1]], []),
    )
    next_state = dataclasses.replace(
        first.after_town,
        market=dataclasses.replace(first.after_town.market, source_step=2),
    )
    second = ledger.apply_inventory_phase(
        next_state,
        ((["PICKUP", "WHEAT", 1], []), (["PASS"], [])),
        ((True,), (True,)),
        ([], []),
    )
    assert second.after_units.units[0][0].entries == (("WHEAT", 1),)


def test_missing_and_excess_hand_actions_match_unit_count():
    model_state = _state(
        first=_account(hands=1),
        first_units=(
            ledger.UnitInventory((("WHEAT", 1),)),
            ledger.UnitInventory((("CARROT", 1),)),
        ),
    )
    missing = ledger.apply_inventory_phase(
        model_state,
        _pass_actions(first_hands=[]),
        ((True, True), (True,)),
        ([], []),
    )
    excess = ledger.apply_inventory_phase(
        model_state,
        _pass_actions(first_hands=[["DROP"], ["DROP"]]),
        ((True, True), (True,)),
        ([], []),
    )
    assert missing.after_town.units[0][1].entries == (("CARROT", 1),)
    assert excess.after_town.units[0][1] == ledger.UnitInventory()
    assert sum(excess.after_town.market.players[0].shed) == 1


def test_day_end_uses_farmer_then_hand_and_discards_overflow():
    model_state = _state(
        first=_account(hands=1, hires_today=2, shed=_shed(WHEAT=1)),
        first_units=(
            ledger.UnitInventory((("CARROT", 2),)),
            ledger.UnitInventory((("MILK", 2),)),
        ),
        capacity=3,
    )
    result = ledger.apply_inventory_day_end(model_state, trace=True)
    player = result.state.market.players[0]
    assert player.shed[market.SHED_ITEMS.index("CARROT")] == 2
    assert player.shed[market.SHED_ITEMS.index("MILK")] == 0
    assert result.discarded[0][market.SHED_ITEMS.index("MILK")] == 2
    assert player.hands == 0
    assert player.hires_today == 0
    assert result.state.units[0] == (ledger.UnitInventory(),)


def test_source_step_23_hire_is_removed_by_composed_day_end():
    model_state = _state(step=23)
    transition = ledger.apply_inventory_phase(
        model_state,
        _pass_actions(),
        ((True,), (True,)),
        ([["HIRE"]], []),
    )
    result = ledger.apply_inventory_day_end(transition.after_town)
    assert transition.after_town.market.players[0].hands == 1
    assert result.state.market.players[0].hands == 0
    assert result.state.units[0] == (ledger.UnitInventory(),)


def test_trace_does_not_change_state():
    model_state = _state(first=_account(shed=_shed(WHEAT=2)))
    arguments = (
        model_state,
        ((["PICKUP", "WHEAT", 1], []), (["PASS"], [])),
        ((True,), (True,)),
        ([], []),
    )
    plain = ledger.apply_inventory_phase(*arguments)
    traced = ledger.apply_inventory_phase(*arguments, trace=True)
    assert plain.after_units == traced.after_units
    assert plain.after_town == traced.after_town
    assert not plain.events
    assert traced.events


def test_unhashable_operation_matches_simulator_exception():
    with pytest.raises(TypeError):
        ledger.apply_inventory_phase(
            _state(),
            (([[]], []), (["PASS"], [])),
            ((True,), (True,)),
            ([], []),
        )


def test_boundary_and_smoke_validation_pass():
    result = validator.run_validation(
        random_cases=100,
        seed=validator.DEFAULT_SEED,
        boundaries=True,
        stop_first=True,
    )
    assert result["processed_fixtures"] == result["fixtures"]
    assert result["mismatches"] == 0
    assert result["unexpected_failures"] == 0


def test_stratified_prefix_has_required_coverage():
    cases = validator._random_cases(validator.DEFAULT_SEED, 100)
    operations = set()
    for case in cases:
        for farmer_action, hands_actions in case.unit_actions:
            if isinstance(farmer_action, list) and farmer_action:
                operations.add(farmer_action[0])
            if isinstance(hands_actions, list):
                operations.update(
                    action[0]
                    for action in hands_actions
                    if isinstance(action, list) and action
                )
    assert set(validator.OPERATIONS) <= operations
    assert {case.state.market.source_step for case in cases} == set(
        validator.SOURCE_STEPS
    )
    assert {case.state.market.config.shed_capacity for case in cases} == {1, 100}
