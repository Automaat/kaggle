import dataclasses
import importlib
import pathlib
import sys

import pytest


TOOLS = pathlib.Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))
animal = importlib.import_module("economics.animal_ledger")
crop = importlib.import_module("economics.crop_ledger")
inventory = importlib.import_module("economics.inventory_ledger")
market = importlib.import_module("economics.market_ledger")
crop_validator = importlib.import_module("economics.validate_crop_ledger")
validator = importlib.import_module("economics.validate_animal_ledger")


def _state(
    step=1,
    hands=0,
    position=(0, 0),
    hand_positions=None,
    cell=None,
    cell_position=(0, 0),
    carried=None,
    shed=None,
    seeds=None,
    shops=(),
):
    account = crop_validator.account(
        shed=shed,
        seeds=seeds,
        hands=hands,
    )
    other = crop_validator.account()
    units = (
        (
            inventory.UnitInventory.from_mapping(carried or {}),
            *tuple(inventory.UnitInventory() for _ in range(hands)),
        ),
        (inventory.UnitInventory(),),
    )
    crop_state = crop_validator.crop_state(step, (account, other), units)
    crop_boards = list(crop_state.boards)
    animal_boards = [animal.AnimalBoard.empty(), animal.AnimalBoard.empty()]
    if cell in ("COOP", "PASTURE"):
        crop_boards[0] = crop_boards[0].set(cell_position, "STRUCTURE")
        animal_boards[0] = animal_boards[0].set(cell_position, cell)
    elif isinstance(cell, animal.AnimalTile):
        crop_boards[0] = crop_boards[0].set(cell_position, "ANIMAL")
        animal_boards[0] = animal_boards[0].set(cell_position, cell)
    crop_state = crop.CropState(crop_state.inventory, tuple(crop_boards))
    if shops:
        model_market = dataclasses.replace(crop_state.inventory.market, shops=shops)
        model_inventory = inventory.InventoryState(
            model_market,
            crop_state.inventory.units,
        )
        crop_state = crop.CropState(model_inventory, crop_state.boards)
    hand_positions = hand_positions or tuple((index + 1, 0) for index in range(hands))
    positions = ((position, *hand_positions), ((0, 1),))
    return animal.AnimalState(crop_state, positions, tuple(animal_boards))


def _phase(state, farmer, hands=None, queues=([], []), config=None):
    return animal.apply_animal_phase(
        state,
        ((farmer, hands or []), (["PASS"], [])),
        queues,
        config or animal.AnimalConfig(weed_chance=0),
        True,
    )


def test_animal_tables_match_simulator():
    from kaggle_environments.envs.kaggriculture import kaggriculture as simulator

    assert set(animal.ANIMAL_SPECS) == set(simulator.ANIMALS)
    for name, spec in animal.ANIMAL_SPECS.items():
        assert dataclasses.asdict(spec) == simulator.ANIMALS[name]


def test_overlay_must_match_crop_board():
    crop_state = crop_validator.crop_state()
    board = animal.AnimalBoard.empty().set((0, 0), "COOP")
    with pytest.raises(ValueError):
        animal.AnimalState(
            crop_state,
            (((0, 0),), ((0, 1),)),
            (board, animal.AnimalBoard.empty()),
        )


def test_movement_updates_position_and_allows_locked_tile():
    state = _state(position=(4, 4))
    east = _phase(state, ["EAST"])
    assert east.after_units.positions[0][0] == (5, 4)
    edge = _phase(_state(position=(0, 0)), ["WEST"])
    assert edge.after_units.positions[0][0] == (0, 0)


def test_build_and_place_animal():
    built = _phase(_state(), ["BUILD_COOP"])
    state = animal.AnimalState(
        crop.CropState(
            built.after_units.crop.inventory,
            built.after_units.crop.boards,
        ),
        built.after_units.positions,
        built.after_units.animal_boards,
    )
    account = state.crop.inventory.market.players[0]
    units = list(state.crop.inventory.units)
    units[0] = (inventory.UnitInventory.from_mapping({"GOOSE": 1}),)
    model_inventory = inventory.InventoryState(state.crop.inventory.market, tuple(units))
    state = animal.AnimalState(
        crop.CropState(model_inventory, state.crop.boards),
        state.positions,
        state.animal_boards,
    )
    placed = _phase(state, ["PLACE", "GOOSE"])
    current = placed.after_units.animal_boards[0].get((0, 0))
    assert isinstance(current, animal.AnimalTile)
    assert current.animal == "GOOSE"
    assert placed.after_units.crop.inventory.units[0][0].entries == ()
    assert account.hands == 0


def test_matching_place_wins_over_shed_place():
    state = _state(
        position=(4, 4),
        cell="PASTURE",
        cell_position=(4, 4),
        carried={"COW": 1},
    )
    result = _phase(state, ["PLACE", "COW"])
    assert isinstance(result.after_units.animal_boards[0].get((4, 4)), animal.AnimalTile)
    assert sum(result.after_units.crop.inventory.market.players[0].shed) == 0


def test_nonmatching_place_uses_shed():
    state = _state(
        position=(4, 4),
        cell="COOP",
        cell_position=(4, 4),
        carried={"COW": 1},
    )
    result = _phase(state, ["PLACE", "COW"])
    shed = result.after_units.crop.inventory.market.players[0].shed_mapping()
    assert result.after_units.animal_boards[0].get((4, 4)) == "COOP"
    assert shed["COW"] == 1


def test_feed_care_and_production_refresh():
    current = dataclasses.replace(
        animal.AnimalTile.create("GOOSE", 0),
        pending_care_bonus=1,
    )
    state = _state(
        step=3 * 24 + 23,
        cell=current,
        carried={"WHEAT": 1},
        hands=1,
        hand_positions=((0, 0),),
    )
    result = _phase(state, ["FEED"], [["CARE"]])
    refreshed = result.after_animal_refresh.animal_boards[0].get((0, 0))
    assert refreshed.yield_units == 2
    assert refreshed.pending_care_bonus == 1
    assert refreshed.fertilizer_available is True


def test_two_unfed_days_remove_animal():
    current = dataclasses.replace(
        animal.AnimalTile.create("SHEEP", 0),
        consecutive_unfed=1,
        yield_units=3,
    )
    result = _phase(_state(step=23, cell=current), ["PASS"])
    assert result.after_animal_refresh.animal_boards[0].get((0, 0)) == "PASTURE"
    assert result.after_animal_refresh.crop.boards[0].get((0, 0)) == "STRUCTURE"


def test_harvest_and_collect_fertilizer():
    current = dataclasses.replace(
        animal.AnimalTile.create("COW", 0),
        yield_units=4,
        fertilizer_available=True,
    )
    harvested = _phase(_state(cell=current), ["HARVEST"])
    carried = harvested.after_units.crop.inventory.units[0][0]
    assert carried.quantity("MILK") == 4
    state = _state(cell=current)
    collected = _phase(state, ["COLLECT_FERTILIZER"])
    carried = collected.after_units.crop.inventory.units[0][0]
    assert carried.quantity("FERTILIZER") == 1


def test_animal_cannot_be_dug():
    current = animal.AnimalTile.create("GOOSE", 0)
    result = _phase(_state(cell=current), ["DIG"])
    assert isinstance(result.after_units.animal_boards[0].get((0, 0)), animal.AnimalTile)


def test_hires_get_exact_spawn_positions():
    state = _state(position=(4, 4))
    result = _phase(state, ["PASS"], queues=([['HIRE']] * 4, []))
    assert result.after_town.positions[0] == (
        (4, 4),
        (5, 4),
        (4, 5),
        (5, 5),
        (4, 4),
    )


def test_end_of_day_resets_units_and_positions():
    result = _phase(
        _state(step=23, hands=1, carried={"WHEAT": 2}),
        ["PASS"],
    )
    assert result.after_end.positions == (((4, 4),), ((4, 4),))
    assert len(result.after_end.crop.inventory.units[0]) == 1
    assert result.after_end.crop.inventory.market.players[0].hands == 0
    assert result.after_end.crop.inventory.market.players[0].shed_mapping()["WHEAT"] == 2


def test_weed_probability_one_fills_only_empty_cells():
    result = _phase(
        _state(step=23, cell="COOP"),
        ["PASS"],
        config=animal.AnimalConfig(episode_seed=7, weed_chance=1),
    )
    assert result.after_weeds.crop.boards[0].get((0, 0)) == "STRUCTURE"
    assert result.after_weeds.crop.boards[0].get((1, 0)) == "WEED"


def test_shop_unlock_uses_same_day_rng():
    result = _phase(
        _state(step=2 * 24 + 23),
        ["PASS"],
        config=animal.AnimalConfig(
            episode_seed=42,
            weed_chance=0,
            shop_unlock_interval=3,
        ),
    )
    assert len(result.after_end.crop.inventory.market.shops) == 1
    assert result.after_end.crop.inventory.market.shops[0] in market.SHOP_DEMAND


def test_partial_state_survives_later_hand_exception():
    state = _state(hands=1, hand_positions=((0, 0),))
    with pytest.raises(TypeError) as caught:
        animal.apply_animal_player(
            state,
            0,
            (["EAST"], [[[]]]),
        )
    assert caught.value.partial_state.positions[0][0] == (1, 0)


def test_advance_increments_source_step_only():
    state = _state(step=717)
    advanced = animal.advance_animal_state(state)
    assert advanced.crop.inventory.market.source_step == 718
    assert advanced.positions == state.positions
    with pytest.raises(ValueError):
        animal.advance_animal_state(advanced)


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
        "animals",
        "operations",
        "players",
        "unit_seats",
        "tile_states",
        "source_steps",
        "weed_regimes",
    ):
        assert all(count > 0 for count in manifest[name].values())
    full = validator.coverage_manifest(
        validator._random_cases(validator.DEFAULT_SEED, 5000)
    )
    assert all(count > 0 for count in full["land_counts"].values())
