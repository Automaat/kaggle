import argparse
import copy
import hashlib
import importlib.metadata
import inspect
import json
import random
import time
from dataclasses import dataclass, fields, is_dataclass, replace
from pathlib import Path

from kaggle_environments import make
from kaggle_environments.envs.kaggriculture import kaggriculture as simulator

try:
    from . import validate_market_ledger as market_validator
    from .animal_ledger import (
        ANIMAL_SPECS,
        AnimalBoard,
        AnimalConfig,
        AnimalState,
        AnimalTile,
        apply_animal_phase,
        apply_animal_player,
    )
    from .crop_ledger import BOARD_SIZE, CropBoard, CropState, PlantState
    from .inventory_ledger import InventoryState, UnitInventory
    from .market_ledger import (
        ANIMALS,
        CROPS,
        DEFAULT_MARKET_PARAMS,
        PRODUCTS,
        SHED_ITEMS,
        MarketConfig,
        MarketState,
        PlayerAccount,
    )
except ImportError:
    import validate_market_ledger as market_validator
    from animal_ledger import (
        ANIMAL_SPECS,
        AnimalBoard,
        AnimalConfig,
        AnimalState,
        AnimalTile,
        apply_animal_phase,
        apply_animal_player,
    )
    from crop_ledger import BOARD_SIZE, CropBoard, CropState, PlantState
    from inventory_ledger import InventoryState, UnitInventory
    from market_ledger import (
        ANIMALS,
        CROPS,
        DEFAULT_MARKET_PARAMS,
        PRODUCTS,
        SHED_ITEMS,
        MarketConfig,
        MarketState,
        PlayerAccount,
    )


GENERATOR_SCHEMA = "animal-v1"
DEFAULT_SEED = 3_980_000
SOURCE_STEPS = (0, 23, 24, 717, 718)
OPERATIONS = (
    "NORTH",
    "SOUTH",
    "EAST",
    "WEST",
    "HARVEST",
    "DIG",
    "BUILD_COOP",
    "BUILD_PASTURE",
    "PLACE",
    "FEED",
    "COLLECT_FERTILIZER",
    "CARE",
    "PICKUP",
    "DROP",
    "PASS",
)
TILE_STATES = ("EMPTY", "COOP", "PASTURE", "ANIMAL", "WEED", "LOCKED")
COMPARED_FIELDS = (
    "positions",
    "animal_boards",
    "crop_state",
    "market_and_town",
    "plant_decay",
    "crop_refresh",
    "animal_refresh",
    "weeds",
    "full_day_end",
    "partial_exception_state",
)


@dataclass(frozen=True, slots=True)
class ValidationCase:
    identifier: str
    layer: str
    state: AnimalState
    unit_actions: tuple[tuple[object, object], tuple[object, object]]
    market_queues: tuple[object, object]
    config: AnimalConfig


def _zero(names):
    return {name: 0 for name in names}


def account(
    money=3000.0,
    shed=None,
    seeds=None,
    hires_today=0,
    unlocked_quadrants=1,
    hands=0,
):
    return PlayerAccount.from_mappings(
        money,
        shed or _zero(SHED_ITEMS),
        seeds or _zero(CROPS),
        hires_today,
        unlocked_quadrants,
        hands,
    )


def animal_state(
    source_step=1,
    players=None,
    units=None,
    positions=None,
    crop_boards=None,
    animal_boards=None,
    config=MarketConfig(),
    shops=(),
):
    players = players or (account(), account())
    market = MarketState.from_mappings(
        source_step,
        {
            item: int(param.I0)
            for item, param in zip(PRODUCTS, DEFAULT_MARKET_PARAMS)
        },
        players,
        shops,
        DEFAULT_MARKET_PARAMS,
        config,
    )
    if units is None:
        units = tuple(
            tuple(UnitInventory() for _ in range(player.hands + 1))
            for player in players
        )
    inventory = InventoryState(market, units)
    if crop_boards is None:
        crop_boards = tuple(
            CropBoard.initial(player.unlocked_quadrants) for player in players
        )
    crop = CropState(inventory, crop_boards)
    if positions is None:
        positions = tuple(
            tuple((index, player_index) for index in range(player.hands + 1))
            for player_index, player in enumerate(players)
        )
    if animal_boards is None:
        animal_boards = (AnimalBoard.empty(), AnimalBoard.empty())
    return AnimalState(crop, positions, animal_boards)


def _plant_to_simulator(plant):
    return {
        "kind": "PLANT",
        "crop": plant.crop,
        "planted_day": plant.planted_day,
        "watered_today": plant.watered_today,
        "consecutive_unwatered": plant.consecutive_unwatered,
        "yield_units": plant.yield_units,
        "max_lifespan_step": plant.max_lifespan_step,
        "fertilized_until_day": plant.fertilized_until_day,
    }


def _animal_to_simulator(tile):
    return {
        "kind": ANIMAL_SPECS[tile.animal].structure,
        "animal": tile.animal,
        "placed_day": tile.placed_day,
        "yield_units": tile.yield_units,
        "consecutive_unfed": tile.consecutive_unfed,
        "fed_today": tile.fed_today,
        "cared_today": tile.cared_today,
        "fertilizer_available": tile.fertilizer_available,
        "pending_care_bonus": tile.pending_care_bonus,
    }


def _cell_to_simulator(crop_cell, animal_cell):
    if isinstance(crop_cell, PlantState):
        return _plant_to_simulator(crop_cell)
    if crop_cell == "WEED":
        return {"kind": "WEED"}
    if isinstance(animal_cell, AnimalTile):
        return _animal_to_simulator(animal_cell)
    if animal_cell in ("COOP", "PASTURE"):
        return {"kind": animal_cell}
    return crop_cell


def _plant_from_simulator(cell):
    return PlantState(
        cell["crop"],
        cell["planted_day"],
        cell["watered_today"],
        cell["consecutive_unwatered"],
        cell["yield_units"],
        cell["max_lifespan_step"],
        cell.get("fertilized_until_day", -1),
    )


def _animal_from_simulator(cell):
    return AnimalTile(
        cell["animal"],
        cell["placed_day"],
        cell["yield_units"],
        cell["consecutive_unfed"],
        cell["fed_today"],
        cell["cared_today"],
        cell["fertilizer_available"],
        cell.get("pending_care_bonus", 0),
    )


def _boards_from_simulator(farm):
    crop_cells = []
    animal_cells = []
    for row in farm["tiles"]:
        for cell in row:
            animal_cell = None
            if cell is None or cell == "LOCKED":
                crop_cell = cell
            elif isinstance(cell, dict) and cell.get("kind") == "PLANT":
                crop_cell = _plant_from_simulator(cell)
            elif isinstance(cell, dict) and cell.get("kind") == "WEED":
                crop_cell = "WEED"
            elif isinstance(cell, dict) and "animal" in cell:
                crop_cell = "ANIMAL"
                animal_cell = _animal_from_simulator(cell)
            else:
                crop_cell = "STRUCTURE"
                animal_cell = cell["kind"]
            crop_cells.append(crop_cell)
            animal_cells.append(animal_cell)
    return CropBoard(tuple(crop_cells)), AnimalBoard(tuple(animal_cells))


def _inject(environment, case):
    config = case.config
    environment.configuration.weedSpawnChance = config.weed_chance
    environment.configuration.townShopUnlockInterval = config.shop_unlock_interval
    market_case = market_validator.ValidationCase(
        case.identifier,
        case.layer,
        case.state.crop.inventory.market,
        case.market_queues,
    )
    reference_state = market_validator._inject(environment, market_case)
    environment.info["seed"] = config.episode_seed
    shared = reference_state[0].observation
    for player in range(2):
        farm = shared.farms[player]
        crop_board = case.state.crop.boards[player]
        animal_board = case.state.animal_boards[player]
        farm["tiles"] = [
            [
                _cell_to_simulator(
                    crop_board.cells[y * BOARD_SIZE + x],
                    animal_board.cells[y * BOARD_SIZE + x],
                )
                for x in range(BOARD_SIZE)
            ]
            for y in range(BOARD_SIZE)
        ]
        positions = case.state.positions[player]
        farm["farmer"] = list(positions[0])
        farm["hands"] = [list(position) for position in positions[1:]]
        private = reference_state[player].observation.private
        private["inventories"] = [
            value.mapping() for value in case.state.crop.inventory.units[player]
        ]
        farmer, hands = case.unit_actions[player]
        reference_state[player].action = {
            "farmer": farmer,
            "hands": hands,
            "market": case.market_queues[player],
        }
    projected = _project(reference_state, case.state.crop.inventory.market)
    if projected != case.state:
        raise AssertionError("injected animal state differs")
    return reference_state


def _project(reference_state, template):
    shared = reference_state[0].observation
    players = []
    units = []
    positions = []
    crop_boards = []
    animal_boards = []
    for player in range(2):
        farm = shared.farms[player]
        private = reference_state[player].observation.private
        players.append(
            PlayerAccount.from_mappings(
                farm["money"],
                {item: private["shed"][item] for item in SHED_ITEMS},
                {item: private["seeds"][item] for item in CROPS},
                farm["hires_today"],
                len(farm["unlocked_quadrants"]),
                len(farm["hands"]),
            )
        )
        units.append(
            tuple(
                UnitInventory.from_mapping(dict(value))
                for value in private["inventories"]
            )
        )
        positions.append(
            (tuple(farm["farmer"]), *tuple(tuple(value) for value in farm["hands"]))
        )
        crop_board, animal_board = _boards_from_simulator(farm)
        crop_boards.append(crop_board)
        animal_boards.append(animal_board)
    market = MarketState.from_mappings(
        template.source_step,
        {item: shared.market["inventory"][item] for item in PRODUCTS},
        tuple(players),
        tuple(shared.town["unlocked_shops"]),
        template.params,
        template.config,
    )
    prices = tuple(shared.market["prices"][item] for item in PRODUCTS)
    if prices != market.prices:
        raise AssertionError("projected prices differ")
    inventory = InventoryState(market, tuple(units))
    crop = CropState(inventory, tuple(crop_boards))
    return AnimalState(crop, tuple(positions), tuple(animal_boards))


def _simulator_player(reference_state, case, player):
    farm = reference_state[0].observation.farms[player]
    private = reference_state[player].observation.private
    farmer_action, raw_hands = case.unit_actions[player]
    hands = raw_hands if isinstance(raw_hands, list) else []
    requests = [farmer_action, *hands]
    demand = {}
    for action in requests:
        if isinstance(action, list) and len(action) >= 2 and action[0] == "PLANT":
            item = action[1]
            demand[item] = demand.get(item, 0) + 1
    blocked = {
        item
        for item, quantity in demand.items()
        if quantity > private["seeds"].get(item, 0)
    }

    def allowed(action):
        if (
            isinstance(action, list)
            and len(action) >= 2
            and action[0] == "PLANT"
            and action[1] in blocked
        ):
            return ["PASS"]
        return action

    day = case.state.crop.inventory.market.source_step // 24
    capacity = case.state.crop.inventory.market.config.shed_capacity
    simulator._apply_unit_action(
        farm,
        private,
        0,
        allowed(farmer_action),
        BOARD_SIZE,
        day,
        24,
        capacity,
    )
    for index, action in enumerate(hands):
        simulator._apply_unit_action(
            farm,
            private,
            index + 1,
            allowed(action),
            BOARD_SIZE,
            day,
            24,
            capacity,
        )


def _typed(value):
    if is_dataclass(value):
        return {
            field.name: _typed(getattr(value, field.name))
            for field in fields(value)
        }
    if type(value) is tuple:
        return {"type": "tuple", "items": [_typed(item) for item in value]}
    if type(value) is list:
        return [_typed(item) for item in value]
    if type(value) is dict:
        return {str(key): _typed(value[key]) for key in sorted(value, key=str)}
    return value


def _mismatch(case, phase, expected, actual, trace=None):
    return {
        "case": _typed(case),
        "phase": phase,
        "expected": _typed(expected),
        "actual": _typed(actual),
        "trace": _typed(trace),
    }


def compare_case(environment, case):
    reference_state = _inject(environment, case)
    model_partial = case.state
    for player in range(2):
        model_error = None
        simulator_error = None
        try:
            model_partial, _ = apply_animal_player(
                model_partial,
                player,
                case.unit_actions[player],
                False,
            )
        except Exception as error:
            model_error = error
            model_partial = getattr(error, "partial_state", model_partial)
        try:
            _simulator_player(reference_state, case, player)
        except Exception as error:
            simulator_error = error
        if model_error is not None or simulator_error is not None:
            actual = _project(reference_state, case.state.crop.inventory.market)
            if (
                model_error is not None
                and simulator_error is not None
                and type(model_error) is type(simulator_error)
                and actual == model_partial
            ):
                return None, 1
            return (
                _mismatch(
                    case,
                    f"player_{player}_exception",
                    {
                        "error": None if model_error is None else type(model_error).__name__,
                        "state": model_partial,
                    },
                    {
                        "error": None
                        if simulator_error is None
                        else type(simulator_error).__name__,
                        "state": actual,
                    },
                ),
                0,
            )
        actual = _project(reference_state, case.state.crop.inventory.market)
        if actual != model_partial:
            return _mismatch(case, f"player_{player}", model_partial, actual), 0
    result = apply_animal_phase(
        case.state,
        case.unit_actions,
        case.market_queues,
        case.config,
        True,
    )
    if result.after_units != model_partial:
        raise AssertionError("public unit composition differs")
    simulator._process_market(reference_state, environment)
    simulator._town_consume(
        environment,
        reference_state,
        case.state.crop.inventory.market.source_step,
    )
    actual_town = _project(reference_state, case.state.crop.inventory.market)
    if actual_town != result.after_town:
        return _mismatch(case, "after_town", result.after_town, actual_town, result), 0
    for farm in reference_state[0].observation.farms:
        simulator._decay_plants(
            farm,
            case.state.crop.inventory.market.source_step,
        )
    actual_decay = _project(reference_state, case.state.crop.inventory.market)
    if actual_decay != result.after_decay:
        return _mismatch(case, "after_decay", result.after_decay, actual_decay, result), 0
    if result.after_end is None:
        return None, 0
    before_end = copy.deepcopy(reference_state)
    day = case.state.crop.inventory.market.source_step // 24
    for farm in reference_state[0].observation.farms:
        simulator._daily_refresh_plants(farm, day, 24)
    actual_crop = _project(reference_state, case.state.crop.inventory.market)
    if actual_crop != result.after_crop_refresh:
        return _mismatch(
            case,
            "after_crop_refresh",
            result.after_crop_refresh,
            actual_crop,
            result,
        ), 0
    for farm in reference_state[0].observation.farms:
        simulator._daily_refresh_animals(farm, day)
    actual_animals = _project(reference_state, case.state.crop.inventory.market)
    if actual_animals != result.after_animal_refresh:
        return _mismatch(
            case,
            "after_animal_refresh",
            result.after_animal_refresh,
            actual_animals,
            result,
        ), 0
    rng = random.Random((case.config.episode_seed * 1_000_003) ^ day)
    for farm in reference_state[0].observation.farms:
        simulator._spawn_weeds(
            farm,
            BOARD_SIZE,
            case.config.weed_chance,
            rng,
        )
    actual_weeds = _project(reference_state, case.state.crop.inventory.market)
    if actual_weeds != result.after_weeds:
        return _mismatch(case, "after_weeds", result.after_weeds, actual_weeds, result), 0
    simulator._end_of_day(before_end, environment, day)
    actual_end = _project(before_end, case.state.crop.inventory.market)
    if actual_end != result.after_end:
        return _mismatch(case, "after_end", result.after_end, actual_end, result), 0
    return None, 0


def _passes(hands=0):
    return (["PASS"], [["PASS"] for _ in range(hands)])


def _replace_player(
    state,
    player,
    account_value=None,
    units=None,
    positions=None,
    crop_board=None,
    animal_board=None,
):
    accounts = list(state.crop.inventory.market.players)
    all_units = list(state.crop.inventory.units)
    all_positions = list(state.positions)
    crop_boards = list(state.crop.boards)
    animal_boards = list(state.animal_boards)
    if account_value is not None:
        accounts[player] = account_value
    if units is not None:
        all_units[player] = units
    if positions is not None:
        all_positions[player] = positions
    if crop_board is not None:
        crop_boards[player] = crop_board
    if animal_board is not None:
        animal_boards[player] = animal_board
    market = replace(state.crop.inventory.market, players=tuple(accounts))
    model_inventory = InventoryState(market, tuple(all_units))
    crop = CropState(model_inventory, tuple(crop_boards))
    return AnimalState(crop, tuple(all_positions), tuple(animal_boards))


def _set_cell(state, player, position, value):
    crop_board = state.crop.boards[player]
    animal_board = state.animal_boards[player]
    if value in ("COOP", "PASTURE"):
        crop_board = crop_board.set(position, "STRUCTURE")
        animal_board = animal_board.set(position, value)
    elif isinstance(value, AnimalTile):
        crop_board = crop_board.set(position, "ANIMAL")
        animal_board = animal_board.set(position, value)
    elif value == "WEED":
        crop_board = crop_board.set(position, "WEED")
    return _replace_player(
        state,
        player,
        crop_board=crop_board,
        animal_board=animal_board,
    )


def _base(source_step=1, hands=1, unlocked=1, capacity=100):
    players = (
        account(hands=hands, unlocked_quadrants=unlocked),
        account(hands=hands, unlocked_quadrants=unlocked),
    )
    return animal_state(
        source_step,
        players,
        config=MarketConfig(shed_capacity=capacity),
    )


def _boundary_cases():
    cases = []
    serial = 0

    def add(name, state, actions, queues=([], []), config=None):
        nonlocal serial
        cases.append(
            ValidationCase(
                f"B{serial:05d}-{name}",
                "boundary",
                state,
                actions,
                queues,
                config or AnimalConfig(weed_chance=0),
            )
        )
        serial += 1

    base = _base()
    passes = (_passes(1), _passes(1))
    add("pass", base, passes)
    for animal in ANIMALS:
        spec = ANIMAL_SPECS[animal]
        for age in sorted(
            {
                0,
                1,
                spec.first_yield_day - 1,
                spec.first_yield_day,
                spec.first_yield_day + spec.interval,
            }
        ):
            step = min(718, age * 24 + 23)
            for fed in (False, True):
                for cared in (False, True):
                    current = AnimalTile(
                        animal,
                        0,
                        min(spec.max_held, 2),
                        0,
                        fed,
                        cared,
                        True,
                        1,
                    )
                    state = _set_cell(_base(step), 0, (0, 0), current)
                    for operation in (
                        "HARVEST",
                        "FEED",
                        "CARE",
                        "COLLECT_FERTILIZER",
                        "DIG",
                        "PASS",
                    ):
                        units = list(state.crop.inventory.units[0])
                        units[0] = UnitInventory.from_mapping(
                            {"WHEAT": 1} if operation == "FEED" else {}
                        )
                        current_state = _replace_player(
                            state,
                            0,
                            units=tuple(units),
                        )
                        add(
                            f"{animal}-{age}-{fed}-{cared}-{operation}",
                            current_state,
                            (([operation], [["PASS"]]), _passes(1)),
                        )
    for structure, animal in (("COOP", "GOOSE"), ("PASTURE", "COW")):
        state = _set_cell(_base(), 0, (4, 4), structure)
        units = list(state.crop.inventory.units[0])
        units[0] = UnitInventory.from_mapping({animal: 1})
        state = _replace_player(
            state,
            0,
            units=tuple(units),
            positions=((4, 4), (0, 0)),
        )
        add(
            f"place-{animal}",
            state,
            ((["PLACE", animal], [["PASS"]]), _passes(1)),
        )
        missing = _replace_player(
            state,
            0,
            units=(UnitInventory(), UnitInventory()),
        )
        add(
            f"place-missing-{animal}",
            missing,
            ((["PLACE", animal], [["PASS"]]), _passes(1)),
        )
    for operation in ("BUILD_COOP", "BUILD_PASTURE", "DIG"):
        add(
            f"structure-{operation}",
            base,
            (([operation], [["PASS"]]), _passes(1)),
        )
    for position, operation in (
        ((0, 0), "NORTH"),
        ((0, 0), "WEST"),
        ((9, 9), "SOUTH"),
        ((9, 9), "EAST"),
        ((4, 4), "EAST"),
    ):
        state = _replace_player(base, 0, positions=(position, (0, 0)))
        add(
            f"move-{position}-{operation}",
            state,
            (([operation], [["PASS"]]), _passes(1)),
        )
    for count in range(1, 6):
        add(
            f"hire-{count}",
            base,
            passes,
            queues=([['HIRE']] * count, []),
        )
    for chance in (0, 1, 0.125):
        for seed in (0, 42):
            add(
                f"weeds-{chance}-{seed}",
                _base(23),
                passes,
                config=AnimalConfig(seed, chance, 3),
            )
    add(
        "shop-unlock",
        _base(2 * 24 + 23),
        passes,
        config=AnimalConfig(42, 0.125, 3),
    )
    full_shops = tuple(sorted(simulator.SHOPS))
    state = _base(2 * 24 + 23)
    model_market = replace(state.crop.inventory.market, shops=full_shops)
    model_inventory = InventoryState(model_market, state.crop.inventory.units)
    state = AnimalState(
        CropState(model_inventory, state.crop.boards),
        state.positions,
        state.animal_boards,
    )
    add(
        "shop-cap",
        state,
        passes,
        config=AnimalConfig(42, 0, 3),
    )
    for raw in ([[]], [[]], [], (), "PASS"):
        add(f"raw-{serial}", base, ((raw, [["PASS"]]), _passes(1)))
    state = _replace_player(base, 0, positions=((0, 0), (0, 0)))
    add(
        "partial-after-move",
        state,
        ((["EAST"], [[[]]]), _passes(1)),
    )
    return cases


def _random_case(seed, index):
    rng = random.Random(seed ^ ((index + 1) * 1_000_003))
    source_step = SOURCE_STEPS[index % len(SOURCE_STEPS)]
    day = source_step // 24
    animal = ANIMALS[index % len(ANIMALS)]
    operation = OPERATIONS[(index // len(ANIMALS)) % len(OPERATIONS)]
    player = (index // (len(ANIMALS) * len(OPERATIONS))) % 2
    unit_index = (index // (len(ANIMALS) * len(OPERATIONS) * 2)) % 2
    unlocked = 1 + (index // 100) % 4
    players = (
        account(hands=1, unlocked_quadrants=unlocked),
        account(hands=1, unlocked_quadrants=unlocked),
    )
    units = []
    for current_player in range(2):
        carried = {}
        if current_player == player:
            carried = {"WHEAT": 2, animal: 1}
        units.append(
            (
                UnitInventory.from_mapping(carried if unit_index == 0 else {}),
                UnitInventory.from_mapping(carried if unit_index == 1 else {}),
            )
        )
    state = animal_state(source_step, players, tuple(units))
    position = (index % 5, (index // 5) % 5)
    positions = list(state.positions)
    target_positions = list(positions[player])
    target_positions[unit_index] = position
    positions[player] = tuple(target_positions)
    state = AnimalState(state.crop, tuple(positions), state.animal_boards)
    tile_state = TILE_STATES[(index // 7) % len(TILE_STATES)]
    if tile_state == "ANIMAL":
        spec = ANIMAL_SPECS[animal]
        placed_day = max(0, day - rng.randrange(spec.first_yield_day + spec.interval + 1))
        cell = AnimalTile(
            animal,
            placed_day,
            rng.randrange(spec.max_held + 1),
            rng.randrange(2),
            bool((index // 11) % 2),
            bool((index // 13) % 2),
            bool((index // 17) % 2),
            rng.randrange(3),
        )
        state = _set_cell(state, player, position, cell)
    elif tile_state in ("COOP", "PASTURE"):
        state = _set_cell(state, player, position, tile_state)
    elif tile_state == "WEED":
        state = _set_cell(state, player, position, "WEED")
    elif tile_state == "LOCKED":
        crop_boards = list(state.crop.boards)
        crop_boards[player] = crop_boards[player].set(position, "LOCKED")
        state = AnimalState(
            CropState(state.crop.inventory, tuple(crop_boards)),
            state.positions,
            state.animal_boards,
        )
    if operation == "PLACE":
        action = [operation, animal]
    elif operation == "PICKUP":
        action = [operation, "WHEAT", 1]
    else:
        action = [operation]
    actions = [list(_passes(1)) for _ in range(2)]
    farmer, hands = actions[player]
    if unit_index == 0:
        farmer = action
    else:
        hands[0] = action
    actions[player] = (farmer, hands)
    actions[1 - player] = _passes(1)
    market_queues = ([], [])
    if index % 101 == 0:
        market_queues = ([['HIRE']], []) if player == 0 else ([], [['HIRE']])
    elif index % 137 == 0:
        market_queues = (
            [["BUY_ANIMAL", animal, 1]],
            [],
        ) if player == 0 else (
            [],
            [["BUY_ANIMAL", animal, 1]],
        )
    weed_regime = (0, 1, 0.005)[(index // 19) % 3]
    config = AnimalConfig(seed + index, weed_regime, 3)
    return ValidationCase(
        f"R{index:05d}",
        "stratified",
        state,
        tuple(actions),
        market_queues,
        config,
    )


def _random_cases(seed, count):
    return [_random_case(seed, index) for index in range(count)]


def coverage_manifest(cases):
    result = {
        "animals": {name: 0 for name in ANIMALS},
        "operations": {name: 0 for name in OPERATIONS},
        "players": {"0": 0, "1": 0},
        "unit_seats": {"0": 0, "1": 0},
        "tile_states": {name: 0 for name in TILE_STATES},
        "source_steps": {str(value): 0 for value in SOURCE_STEPS},
        "weed_regimes": {"0": 0, "0.005": 0, "1": 0},
        "land_counts": {str(value): 0 for value in range(1, 5)},
    }
    for index, case in enumerate(cases):
        animal = ANIMALS[index % len(ANIMALS)]
        operation = OPERATIONS[(index // len(ANIMALS)) % len(OPERATIONS)]
        player = (index // (len(ANIMALS) * len(OPERATIONS))) % 2
        unit_index = (index // (len(ANIMALS) * len(OPERATIONS) * 2)) % 2
        tile_state = TILE_STATES[(index // 7) % len(TILE_STATES)]
        result["animals"][animal] += 1
        result["operations"][operation] += 1
        result["players"][str(player)] += 1
        result["unit_seats"][str(unit_index)] += 1
        result["tile_states"][tile_state] += 1
        step = case.state.crop.inventory.market.source_step
        result["source_steps"][str(step)] += 1
        result["weed_regimes"][str(case.config.weed_chance)] += 1
        land = case.state.crop.inventory.market.players[player].unlocked_quadrants
        result["land_counts"][str(land)] += 1
    return result


def _source_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run_validation(
    random_cases=5_000,
    seed=DEFAULT_SEED,
    boundaries=True,
    stop_first=False,
):
    cases = _boundary_cases() if boundaries else []
    boundary_count = len(cases)
    random_values = _random_cases(seed, random_cases)
    manifest = coverage_manifest(random_values)
    cases.extend(random_values)
    encoded_inputs = json.dumps(
        {
            "generator_schema": GENERATOR_SCHEMA,
            "seed": seed,
            "coverage": manifest,
            "cases": [_typed(case) for case in cases],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    started = time.perf_counter()
    environment = make("kaggriculture", configuration={"episodeSteps": 720})
    first_mismatch = None
    first_failure = None
    mismatches = 0
    unexpected_failures = 0
    matched_expected_exceptions = 0
    processed = 0
    for case in cases:
        processed += 1
        try:
            mismatch, matched = compare_case(environment, case)
            matched_expected_exceptions += matched
        except Exception as error:
            unexpected_failures += 1
            if first_failure is None:
                first_failure = {
                    "case": _typed(case),
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
            if stop_first:
                break
            continue
        if mismatch is not None:
            mismatches += 1
            if first_mismatch is None:
                first_mismatch = mismatch
            if stop_first:
                break
    elapsed = time.perf_counter() - started
    simulator_path = inspect.getsourcefile(simulator)
    model_path = Path(__file__).with_name("animal_ledger.py")
    crop_path = Path(__file__).with_name("crop_ledger.py")
    return {
        "schema": 1,
        "generator_schema": GENERATOR_SCHEMA,
        "seed": seed,
        "boundary_cases": boundary_count,
        "random_cases": random_cases,
        "fixtures": len(cases),
        "processed_fixtures": processed,
        "coverage": manifest,
        "compared_fields": list(COMPARED_FIELDS),
        "matched_expected_exceptions": matched_expected_exceptions,
        "mismatches": mismatches,
        "unexpected_failures": unexpected_failures,
        "first_mismatch": first_mismatch,
        "first_failure": first_failure,
        "elapsed_seconds": elapsed,
        "environment_version": importlib.metadata.version("kaggle-environments"),
        "input_sha256": hashlib.sha256(encoded_inputs).hexdigest(),
        "model_sha256": _source_hash(model_path),
        "crop_model_sha256": _source_hash(crop_path),
        "simulator_sha256": _source_hash(simulator_path),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--random-cases", type=int, default=5_000)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--skip-boundaries", action="store_true")
    parser.add_argument("--stop-first", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()
    result = run_validation(
        args.random_cases,
        args.seed,
        not args.skip_boundaries,
        args.stop_first,
    )
    encoded = json.dumps(result, indent=2, sort_keys=True)
    print(encoded)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded + "\n")
    if result["mismatches"] or result["unexpected_failures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
