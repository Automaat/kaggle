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
    from .crop_ledger import (
        BOARD_SIZE,
        CROP_SPECS,
        CropBoard,
        CropState,
        PlantState,
        apply_crop_phase,
        apply_crop_player,
    )
    from .inventory_ledger import (
        InventoryState,
        UnitInventory,
        apply_inventory_day_end,
    )
    from .market_ledger import (
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
    from crop_ledger import (
        BOARD_SIZE,
        CROP_SPECS,
        CropBoard,
        CropState,
        PlantState,
        apply_crop_phase,
        apply_crop_player,
    )
    from inventory_ledger import (
        InventoryState,
        UnitInventory,
        apply_inventory_day_end,
    )
    from market_ledger import (
        CROPS,
        DEFAULT_MARKET_PARAMS,
        PRODUCTS,
        SHED_ITEMS,
        MarketConfig,
        MarketState,
        PlayerAccount,
    )


GENERATOR_SCHEMA = "crop-v1"
DEFAULT_SEED = 3_970_000
SOURCE_STEPS = (0, 23, 24, 717, 718)
OPERATIONS = ("PLANT", "WATER", "HARVEST", "FERTILIZE", "DIG", "PASS")
CELL_KINDS = ("EMPTY", "WEED", "PLANT", "LOCKED", "STRUCTURE", "ANIMAL")
COMPARED_FIELDS = (
    "after_units.inventory",
    "after_units.boards",
    "after_town.inventory",
    "after_town.boards",
    "after_decay.boards",
    "after_refresh.boards",
    "partial_exception_state",
)


@dataclass(frozen=True, slots=True)
class ValidationCase:
    identifier: str
    layer: str
    state: CropState
    unit_actions: tuple[tuple[object, object], tuple[object, object]]
    unit_positions: tuple[tuple[tuple[int, int], ...], tuple[tuple[int, int], ...]]
    animal_place_priority: tuple[tuple[bool, ...], tuple[bool, ...]]
    market_queues: tuple[object, object]


def _zero_mapping(names):
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
        shed or _zero_mapping(SHED_ITEMS),
        seeds or _zero_mapping(CROPS),
        hires_today,
        unlocked_quadrants,
        hands,
    )


def crop_state(
    source_step=1,
    players=None,
    units=None,
    boards=None,
    config=MarketConfig(),
):
    players = players or (account(), account())
    market = MarketState.from_mappings(
        source_step,
        {
            item: int(param.I0)
            for item, param in zip(PRODUCTS, DEFAULT_MARKET_PARAMS)
        },
        players,
        (),
        DEFAULT_MARKET_PARAMS,
        config,
    )
    if units is None:
        units = tuple(
            tuple(UnitInventory() for _ in range(player.hands + 1))
            for player in players
        )
    inventory = InventoryState(market, units)
    if boards is None:
        boards = tuple(
            CropBoard.initial(player.unlocked_quadrants) for player in players
        )
    return CropState(inventory, boards)


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


def _cell_to_simulator(cell):
    if isinstance(cell, PlantState):
        return _plant_to_simulator(cell)
    if cell == "WEED":
        return {"kind": "WEED"}
    if cell == "STRUCTURE":
        return {"kind": "COOP"}
    if cell == "ANIMAL":
        return simulator._new_animal("GOOSE", 0)
    return cell


def _cell_from_simulator(cell):
    if cell is None or cell == "LOCKED":
        return cell
    if isinstance(cell, dict) and cell.get("kind") == "PLANT":
        return PlantState(
            cell["crop"],
            cell["planted_day"],
            cell["watered_today"],
            cell["consecutive_unwatered"],
            cell["yield_units"],
            cell["max_lifespan_step"],
            cell.get("fertilized_until_day", -1),
        )
    if isinstance(cell, dict) and cell.get("kind") == "WEED":
        return "WEED"
    if isinstance(cell, dict) and "animal" in cell:
        return "ANIMAL"
    return "STRUCTURE"


def _inject(environment, case):
    environment.configuration.weedSpawnChance = 0.0
    environment.configuration.townShopUnlockInterval = 1000
    market_case = market_validator.ValidationCase(
        case.identifier,
        case.layer,
        case.state.inventory.market,
        case.market_queues,
    )
    reference_state = market_validator._inject(environment, market_case)
    shared = reference_state[0].observation
    for player in range(2):
        farm = shared.farms[player]
        board = case.state.boards[player]
        farm["tiles"] = [
            [
                _cell_to_simulator(board.cells[y * BOARD_SIZE + x])
                for x in range(BOARD_SIZE)
            ]
            for y in range(BOARD_SIZE)
        ]
        positions = case.unit_positions[player]
        farm["farmer"] = list(positions[0])
        farm["hands"] = [list(position) for position in positions[1:]]
        private = reference_state[player].observation.private
        private["inventories"] = [
            inventory.mapping() for inventory in case.state.inventory.units[player]
        ]
        farmer_action, hands_actions = case.unit_actions[player]
        reference_state[player].action = {
            "farmer": farmer_action,
            "hands": hands_actions,
            "market": case.market_queues[player],
        }
    if _project(reference_state, case.state.inventory.market) != case.state:
        raise AssertionError("injected crop state differs")
    return reference_state


def _project_inventory(reference_state, template):
    market, prices = market_validator._project(reference_state, template)
    if prices != market.prices:
        raise AssertionError("projected prices differ")
    units = []
    for player in range(2):
        private = reference_state[player].observation.private
        units.append(
            tuple(
                UnitInventory.from_mapping(dict(inventory))
                for inventory in private["inventories"]
            )
        )
    return InventoryState(market, (units[0], units[1]))


def _project(reference_state, template):
    inventory = _project_inventory(reference_state, template)
    farms = reference_state[0].observation.farms
    boards = []
    for player in range(2):
        boards.append(
            CropBoard(
                tuple(
                    _cell_from_simulator(farms[player]["tiles"][y][x])
                    for y in range(BOARD_SIZE)
                    for x in range(BOARD_SIZE)
                )
            )
        )
    return CropState(inventory, (boards[0], boards[1]))


def _simulator_player(reference_state, case, player):
    farm = reference_state[0].observation.farms[player]
    private = reference_state[player].observation.private
    farmer_action, raw_hands = case.unit_actions[player]
    hands_actions = raw_hands if isinstance(raw_hands, list) else []
    actions = [farmer_action, *hands_actions]
    plant_demand = {}
    for action in actions:
        if isinstance(action, list) and len(action) >= 2 and action[0] == "PLANT":
            crop = action[1]
            plant_demand[crop] = plant_demand.get(crop, 0) + 1
    blocked = {
        crop
        for crop, quantity in plant_demand.items()
        if quantity > private["seeds"].get(crop, 0)
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

    day = case.state.inventory.market.source_step // 24
    capacity = case.state.inventory.market.config.shed_capacity
    simulator._apply_unit_action(
        farm,
        private,
        0,
        allowed(farmer_action),
        10,
        day,
        24,
        capacity,
    )
    for index, action in enumerate(hands_actions):
        simulator._apply_unit_action(
            farm,
            private,
            index + 1,
            allowed(action),
            10,
            day,
            24,
            capacity,
        )


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
            model_partial, _ = apply_crop_player(
                model_partial,
                player,
                case.unit_actions[player],
                case.unit_positions[player],
                case.animal_place_priority[player],
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
            actual_partial = _project(reference_state, case.state.inventory.market)
            if (
                model_error is not None
                and simulator_error is not None
                and type(model_error) is type(simulator_error)
                and actual_partial == model_partial
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
                        "state": actual_partial,
                    },
                ),
                0,
            )
        actual_partial = _project(reference_state, case.state.inventory.market)
        if actual_partial != model_partial:
            return _mismatch(case, f"player_{player}", model_partial, actual_partial), 0
    result = apply_crop_phase(
        case.state,
        case.unit_actions,
        case.unit_positions,
        case.animal_place_priority,
        case.market_queues,
        trace=True,
    )
    if result.after_units != model_partial:
        raise AssertionError("public unit composition differs")
    simulator._process_market(reference_state, environment)
    simulator._town_consume(
        environment,
        reference_state,
        case.state.inventory.market.source_step,
    )
    actual_town = _project(reference_state, case.state.inventory.market)
    if actual_town != result.after_town:
        return _mismatch(case, "after_town", result.after_town, actual_town, result), 0
    for farm in reference_state[0].observation.farms:
        simulator._decay_plants(farm, case.state.inventory.market.source_step)
    actual_decay = _project(reference_state, case.state.inventory.market)
    if actual_decay != result.after_decay:
        return _mismatch(case, "after_decay", result.after_decay, actual_decay, result), 0
    if result.after_refresh is None:
        return None, 0
    before_end = copy.deepcopy(reference_state)
    day = case.state.inventory.market.source_step // 24
    for farm in reference_state[0].observation.farms:
        simulator._daily_refresh_plants(farm, day, 24)
    actual_refresh = _project(reference_state, case.state.inventory.market)
    if actual_refresh != result.after_refresh:
        return _mismatch(
            case,
            "after_refresh",
            result.after_refresh,
            actual_refresh,
            result,
        ), 0
    expected_end_inventory = apply_inventory_day_end(result.after_refresh.inventory).state
    expected_end = CropState(expected_end_inventory, result.after_refresh.boards)
    simulator._end_of_day(before_end, environment, day)
    actual_end = _project(before_end, case.state.inventory.market)
    if actual_end != expected_end:
        return _mismatch(case, "full_day_end", expected_end, actual_end, result), 0
    return None, 0


def _passes(hands=1):
    return (["PASS"], [["PASS"] for _ in range(hands)])


def _base(source_step=1, hands=1, unlocked=1, capacity=100):
    players = (
        account(hands=hands, unlocked_quadrants=unlocked),
        account(hands=hands, unlocked_quadrants=unlocked),
    )
    return crop_state(
        source_step,
        players,
        config=MarketConfig(shed_capacity=capacity),
    )


def _replace_player(state, player, account_value=None, units=None, board=None):
    accounts = list(state.inventory.market.players)
    all_units = list(state.inventory.units)
    boards = list(state.boards)
    if account_value is not None:
        accounts[player] = account_value
    if units is not None:
        all_units[player] = units
    if board is not None:
        boards[player] = board
    market = replace(state.inventory.market, players=tuple(accounts))
    inventory = InventoryState(market, tuple(all_units))
    return CropState(inventory, tuple(boards))


def _with_seeds(account_value, **values):
    seeds = _zero_mapping(CROPS)
    seeds.update(values)
    return replace(account_value, seeds=tuple(seeds[crop] for crop in CROPS))


def _boundary_cases():
    cases = []
    serial = 0

    def add(name, state, actions, positions=None, queues=([], [])):
        nonlocal serial
        hands = tuple(len(state.inventory.units[player]) for player in range(2))
        if positions is None:
            positions = tuple(
                tuple((index, player) for index in range(hands[player]))
                for player in range(2)
            )
        cases.append(
            ValidationCase(
                f"B{serial:05d}-{name}",
                "boundary",
                state,
                actions,
                positions,
                tuple(tuple(False for _ in range(count)) for count in hands),
                queues,
            )
        )
        serial += 1

    base = _base()
    passes = (_passes(), _passes())
    add("pass", base, passes)
    for crop in CROPS:
        spec = CROP_SPECS[crop]
        for age in sorted(
            {
                0,
                1,
                spec.first_yield_day - 1,
                spec.first_yield_day,
                spec.max_yield_day,
                spec.max_yield_day + 1,
            }
        ):
            if age < 0:
                continue
            step = min(718, age * 24 + 23)
            current = _base(step)
            plant = PlantState.create(crop, 0)
            plant = replace(
                plant,
                watered_today=True,
                consecutive_unwatered=0,
                yield_units=(
                    0
                    if spec.ongoing and age < spec.first_yield_day
                    else min(spec.max_yield, 2)
                ),
            )
            board = current.boards[0].set((0, 0), plant)
            current = _replace_player(current, 0, board=board)
            for operation in ("WATER", "HARVEST", "FERTILIZE", "DIG", "PASS"):
                add(
                    f"{crop}-{age}-{operation}",
                    current,
                    (([operation], [["PASS"]]), _passes()),
                )
    for crop in CROPS:
        current = _base(1)
        player = _with_seeds(current.inventory.market.players[0], **{crop: 1})
        current = _replace_player(current, 0, account_value=player)
        add(
            f"plant-{crop}",
            current,
            ((["PLANT", crop], [["WATER"]]), _passes()),
            (((0, 0), (0, 0)), ((0, 1), (1, 1))),
        )
        add(
            f"atomic-{crop}",
            current,
            ((["PLANT", crop], [["PLANT", crop]]), _passes()),
        )
        add(
            f"atomic-excess-{crop}",
            current,
            ((["PLANT", crop], [["PASS"], ["PLANT", crop]]), _passes()),
        )
    for raw in (
        ["PLANT"],
        ["PLANT", "UNKNOWN"],
        ["PLANT", []],
        [[]],
        [],
        (),
        "PLANT",
    ):
        add(f"raw-{serial}", base, ((raw, []), _passes()))
    current = _base(hands=1)
    account_value = current.inventory.market.players[0]
    shed = list(account_value.shed)
    shed[SHED_ITEMS.index("WHEAT")] = 1
    current = _replace_player(
        current,
        0,
        account_value=replace(account_value, shed=tuple(shed)),
    )
    add(
        "partial-after-farmer",
        current,
        ((["PICKUP", "WHEAT", 1], [[[]]]), _passes()),
        (((4, 4), (0, 0)), ((0, 1), (1, 1))),
    )
    current = _base(23)
    for crop in CROPS:
        player = _with_seeds(current.inventory.market.players[0], **{crop: 1})
        seeded = _replace_player(current, 0, account_value=player)
        add(
            f"planting-day-refresh-{crop}",
            seeded,
            ((["PLANT", crop], [["WATER"]]), _passes()),
            (((0, 0), (0, 0)), ((0, 1), (1, 1))),
        )
    for unlocked in range(1, 4):
        current = _base(1, unlocked=unlocked)
        add(
            f"buy-land-{unlocked}",
            current,
            passes,
            queues=([['BUY_LAND']], []),
        )
    for step in SOURCE_STEPS:
        add(f"phase-{step}", _base(step), passes)
    return cases


def _stratified_case(seed, index):
    rng = random.Random(seed ^ ((index + 1) * 1_000_003))
    crop = CROPS[index % len(CROPS)]
    operation = OPERATIONS[(index // 5) % len(OPERATIONS)]
    player = (index // 30) % 2
    unit_index = (index // 60) % 2
    source_step = SOURCE_STEPS[(index // 6) % len(SOURCE_STEPS)]
    day = source_step // 24
    unlocked = 1 + (index // 120) % 4
    players = [
        account(hands=1, unlocked_quadrants=unlocked),
        account(hands=1, unlocked_quadrants=unlocked),
    ]
    seeds = _zero_mapping(CROPS)
    seeds[crop] = rng.randint(0, 3)
    players[player] = replace(
        players[player],
        seeds=tuple(seeds[name] for name in CROPS),
    )
    units = [
        [UnitInventory(), UnitInventory()],
        [UnitInventory(), UnitInventory()],
    ]
    if operation == "FERTILIZE":
        units[player][unit_index] = UnitInventory((("FERTILIZER", rng.randint(1, 3)),))
    boards = [CropBoard.initial(unlocked), CropBoard.initial(unlocked)]
    position = (unit_index, player)
    kind = CELL_KINDS[(index // 2) % len(CELL_KINDS)]
    if operation == "PLANT":
        kind = "EMPTY"
    if operation in ("WATER", "HARVEST", "FERTILIZE"):
        kind = "PLANT"
    if operation == "DIG" and kind == "STRUCTURE":
        kind = "WEED"
    if kind == "WEED":
        boards[player] = boards[player].set(position, "WEED")
    elif kind == "LOCKED":
        boards[player] = boards[player].set(position, "LOCKED")
    elif kind in ("STRUCTURE", "ANIMAL"):
        boards[player] = boards[player].set(position, kind)
    elif kind == "PLANT":
        planted_day = max(0, day - rng.randint(0, CROP_SPECS[crop].max_yield_day + 2))
        plant = PlantState.create(crop, planted_day)
        plant = replace(
            plant,
            watered_today=bool(index % 2),
            consecutive_unwatered=index % 2,
            yield_units=(
                0
                if CROP_SPECS[crop].ongoing
                and day - planted_day < CROP_SPECS[crop].first_yield_day
                else rng.randint(0, CROP_SPECS[crop].max_yield)
            ),
            fertilized_until_day=day + (index % 4) - 2,
        )
        boards[player] = boards[player].set(position, plant)
    state = crop_state(
        source_step,
        tuple(players),
        (tuple(units[0]), tuple(units[1])),
        (boards[0], boards[1]),
    )
    action = [operation]
    if operation == "PLANT":
        action.append(crop)
    actions = [list(_passes()), list(_passes())]
    if unit_index == 0:
        actions[player][0] = action
    else:
        actions[player][1] = [action]
    positions = [((0, 0), (1, 0)), ((0, 1), (1, 1))]
    positions[player] = tuple(
        position if current == unit_index else positions[player][current]
        for current in range(2)
    )
    market_queues = [[], []]
    variant = index % 4
    if variant == 1:
        market_queues[player] = [["BUY_SEED", crop, 1]]
    elif variant == 2 and unlocked < 4:
        market_queues[player] = [["BUY_LAND"]]
    elif variant == 3:
        market_queues[player] = [["HIRE"]]
    return ValidationCase(
        f"R{index:05d}",
        "stratified",
        state,
        (tuple(actions[0]), tuple(actions[1])),
        (tuple(positions[0]), tuple(positions[1])),
        ((False, False), (False, False)),
        (market_queues[0], market_queues[1]),
    )


def _random_cases(seed, count):
    return [_stratified_case(seed, index) for index in range(count)]


def coverage_manifest(cases):
    return _coverage_counts(cases)


def _coverage_counts(cases):
    result = {
        "crops": {crop: 0 for crop in CROPS},
        "operations": {operation: 0 for operation in OPERATIONS},
        "players": {"0": 0, "1": 0},
        "unit_seats": {"0": 0, "1": 0},
        "source_steps": {str(step): 0 for step in SOURCE_STEPS},
        "cell_kinds": {kind: 0 for kind in CELL_KINDS},
        "water_states": {"watered": 0, "dry": 0, "not_plant": 0},
        "fertilizer_states": {"active": 0, "inactive": 0, "not_plant": 0},
        "land_counts": {str(count): 0 for count in range(1, 5)},
    }
    for index, case in enumerate(cases):
        crop = CROPS[index % len(CROPS)]
        operation = OPERATIONS[(index // 5) % len(OPERATIONS)]
        player = (index // 30) % 2
        unit_index = (index // 60) % 2
        result["crops"][crop] += 1
        result["operations"][operation] += 1
        result["players"][str(player)] += 1
        result["unit_seats"][str(unit_index)] += 1
        result["source_steps"][str(case.state.inventory.market.source_step)] += 1
        cell = case.state.boards[player].get(case.unit_positions[player][unit_index])
        if isinstance(cell, PlantState):
            kind = "PLANT"
            water_state = "watered" if cell.watered_today else "dry"
            day = case.state.inventory.market.source_step // 24
            fertilizer_state = "active" if cell.fertilized_until_day >= day else "inactive"
        else:
            kind = "EMPTY" if cell is None else cell
            water_state = "not_plant"
            fertilizer_state = "not_plant"
        result["cell_kinds"][kind] += 1
        result["water_states"][water_state] += 1
        result["fertilizer_states"][fertilizer_state] += 1
        land = case.state.inventory.market.players[player].unlocked_quadrants
        result["land_counts"][str(land)] += 1
    return result


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
    model_path = Path(__file__).with_name("crop_ledger.py")
    inventory_path = Path(__file__).with_name("inventory_ledger.py")
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
        "inventory_model_sha256": _source_hash(inventory_path),
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
