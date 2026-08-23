import dataclasses
import importlib
import pathlib
import sys

import pytest


TOOLS = pathlib.Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))
crop = importlib.import_module("economics.crop_ledger")
inventory = importlib.import_module("economics.inventory_ledger")
market = importlib.import_module("economics.market_ledger")
validator = importlib.import_module("economics.validate_crop_ledger")


def _state(step=1, crop_name=None, plant=None, seeds=0, fertilizer=0, hands=0):
    seed_values = {name: 0 for name in market.CROPS}
    if crop_name is not None:
        seed_values[crop_name] = seeds
    account = market.PlayerAccount.from_mappings(
        3000.0,
        {item: 0 for item in market.SHED_ITEMS},
        seed_values,
        0,
        1,
        hands,
    )
    other = market.PlayerAccount.from_mappings(
        3000.0,
        {item: 0 for item in market.SHED_ITEMS},
        {name: 0 for name in market.CROPS},
        0,
        1,
        0,
    )
    market_state = market.MarketState.from_mappings(
        step,
        {
            item: int(param.I0)
            for item, param in zip(market.PRODUCTS, market.DEFAULT_MARKET_PARAMS)
        },
        (account, other),
    )
    carried = inventory.UnitInventory()
    if fertilizer:
        carried = inventory.UnitInventory((("FERTILIZER", fertilizer),))
    inventories = (
        (carried, *tuple(inventory.UnitInventory() for _ in range(hands))),
        (inventory.UnitInventory(),),
    )
    inventory_state = inventory.InventoryState(market_state, inventories)
    board = crop.CropBoard.initial()
    if plant is not None:
        board = board.set((0, 0), plant)
    return crop.CropState(inventory_state, (board, crop.CropBoard.initial()))


def _phase(state, farmer, hands=None, queues=([], []), positions=None):
    if hands is None:
        hands = []
    if positions is None:
        positions = tuple((index, 0) for index in range(len(state.inventory.units[0])))
    return crop.apply_crop_phase(
        state,
        ((farmer, hands), (["PASS"], [])),
        (positions, ((0, 1),)),
        (
            tuple(False for _ in state.inventory.units[0]),
            (False,),
        ),
        queues,
    )


def test_crop_tables_match_simulator():
    from kaggle_environments.envs.kaggriculture import kaggriculture as simulator

    assert set(crop.CROP_SPECS) == set(simulator.CROPS)
    for name, spec in crop.CROP_SPECS.items():
        assert dataclasses.asdict(spec) == simulator.CROPS[name]


@pytest.mark.parametrize(
    "name,latest",
    (
        ("WHEAT", 27),
        ("CARROT", 27),
        ("TOMATO", 21),
        ("STRAWBERRY", 19),
        ("MELON", 19),
    ),
)
def test_latest_maturing_plant_day(name, latest):
    assert crop.latest_maturing_plant_day(name) == latest


def test_latest_maturing_plant_day_can_be_impossible():
    assert crop.latest_maturing_plant_day("MELON", 9) is None


def test_strawberry_production_days_stop_at_four():
    assert crop.scheduled_production_days("STRAWBERRY", 0) == (10, 12, 14, 16)
    assert crop.scheduled_production_days("STRAWBERRY", 19) == (29,)


def test_plant_and_same_turn_water_survives_refresh():
    state = _state(step=23, crop_name="WHEAT", seeds=1, hands=1)
    result = _phase(
        state,
        ["PLANT", "WHEAT"],
        [["WATER"]],
        positions=((0, 0), (0, 0)),
    )
    plant = result.after_refresh.boards[0].get((0, 0))
    assert isinstance(plant, crop.PlantState)
    assert plant.watered_today is False
    assert plant.consecutive_unwatered == 0


def test_atomic_planting_blocks_all_requests():
    state = _state(crop_name="CARROT", seeds=1, hands=1)
    result = _phase(
        state,
        ["PLANT", "CARROT"],
        [["PLANT", "CARROT"]],
        positions=((0, 0), (1, 0)),
    )
    assert result.after_units.boards[0].get((0, 0)) is None
    assert result.after_units.boards[0].get((1, 0)) is None
    assert result.after_units.inventory.market.players[0].seeds[1] == 1


def test_excess_hand_action_participates_in_atomic_planting():
    state = _state(crop_name="WHEAT", seeds=1)
    result = _phase(
        state,
        ["PLANT", "WHEAT"],
        [["PASS"], ["PLANT", "WHEAT"]],
    )
    assert result.after_units.boards[0].get((0, 0)) is None


def test_fertilize_before_water_doubles_one_time_bonus():
    plant = dataclasses.replace(crop.PlantState.create("WHEAT", 0), yield_units=1)
    state = _state(step=48, plant=plant, fertilizer=1, hands=1)
    result = _phase(
        state,
        ["FERTILIZE"],
        [["WATER"]],
        positions=((0, 0), (0, 0)),
    )
    updated = result.after_units.boards[0].get((0, 0))
    assert updated.yield_units == 3
    assert updated.fertilized_until_day == 4


def test_water_before_fertilize_adds_plain_bonus():
    plant = dataclasses.replace(crop.PlantState.create("WHEAT", 0), yield_units=1)
    state = _state(step=48, plant=plant, fertilizer=1, hands=1)
    result = _phase(
        state,
        ["WATER"],
        [["FERTILIZE"]],
        positions=((0, 0), (0, 0)),
    )
    assert result.after_units.boards[0].get((0, 0)).yield_units == 2


def test_harvest_moves_yield_and_removes_one_time_crop():
    plant = dataclasses.replace(crop.PlantState.create("CARROT", 0), yield_units=4)
    result = _phase(_state(step=48, plant=plant), ["HARVEST"])
    assert result.after_units.boards[0].get((0, 0)) is None
    assert result.after_units.inventory.units[0][0].entries == (("CARROT", 4),)


def test_ongoing_harvest_keeps_plant():
    plant = dataclasses.replace(crop.PlantState.create("TOMATO", 0), yield_units=3)
    result = _phase(_state(step=8 * 24, plant=plant), ["HARVEST"])
    updated = result.after_units.boards[0].get((0, 0))
    assert updated.yield_units == 0
    assert result.after_units.inventory.units[0][0].entries == (("TOMATO", 3),)


def test_ongoing_refresh_produces_and_sets_decay_start():
    plant = dataclasses.replace(
        crop.PlantState.create("TOMATO", 0),
        watered_today=True,
        consecutive_unwatered=0,
        fertilized_until_day=10,
    )
    state = _state(step=10 * 24 + 23, plant=plant)
    refreshed, _ = crop.apply_crop_refresh(state)
    updated = refreshed.boards[0].get((0, 0))
    assert updated.yield_units == 2
    assert updated.max_lifespan_step == 12 * 24


def test_decay_uses_exact_parity():
    plant = dataclasses.replace(
        crop.PlantState.create("WHEAT", 0),
        yield_units=2,
        max_lifespan_step=120,
    )
    unchanged, _ = crop.apply_crop_decay(_state(step=121, plant=plant))
    changed, _ = crop.apply_crop_decay(_state(step=122, plant=plant))
    assert unchanged.boards[0].get((0, 0)).yield_units == 2
    assert changed.boards[0].get((0, 0)).yield_units == 1


def test_buy_land_unlocks_after_units():
    result = _phase(_state(), ["PASS"], queues=([['BUY_LAND']], []))
    assert result.after_units.boards[0].get((5, 0)) == "LOCKED"
    assert result.after_town.boards[0].get((5, 0)) is None


def test_current_turn_seed_purchase_cannot_serve_plant():
    result = _phase(
        _state(crop_name="WHEAT", seeds=0),
        ["PLANT", "WHEAT"],
        queues=([['BUY_SEED', 'WHEAT', 1]], []),
    )
    assert result.after_units.boards[0].get((0, 0)) is None
    assert result.after_town.inventory.market.players[0].seeds[0] == 1


def test_source_step_718_has_no_refresh():
    result = _phase(_state(step=718), ["PASS"])
    assert result.after_refresh is None


def test_boundary_and_smoke_validation_pass():
    result = validator.run_validation(100, validator.DEFAULT_SEED, True, True)
    assert result["processed_fixtures"] == result["fixtures"]
    assert result["mismatches"] == 0
    assert result["unexpected_failures"] == 0


def test_smoke_coverage_has_every_declared_value():
    manifest = validator.coverage_manifest(
        validator._random_cases(validator.DEFAULT_SEED, 100)
    )
    for name in (
        "crops",
        "operations",
        "players",
        "unit_seats",
        "source_steps",
        "cell_kinds",
        "water_states",
        "fertilizer_states",
    ):
        assert all(count > 0 for count in manifest[name].values())
    full = validator.coverage_manifest(
        validator._random_cases(validator.DEFAULT_SEED, 5000)
    )
    assert all(count > 0 for count in full["land_counts"].values())
