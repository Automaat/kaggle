import argparse
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
    from .inventory_ledger import (
        InventoryState,
        UnitInventory,
        apply_inventory_day_end,
        apply_inventory_phase,
    )
    from .market_ledger import (
        CROPS,
        DEFAULT_MARKET_PARAMS,
        PRODUCTS,
        SHED_ITEMS,
        MarketConfig,
        MarketState,
        PlayerAccount,
        apply_market_phase,
    )
except ImportError:
    import validate_market_ledger as market_validator
    from inventory_ledger import (
        InventoryState,
        UnitInventory,
        apply_inventory_day_end,
        apply_inventory_phase,
    )
    from market_ledger import (
        CROPS,
        DEFAULT_MARKET_PARAMS,
        PRODUCTS,
        SHED_ITEMS,
        MarketConfig,
        MarketState,
        PlayerAccount,
        apply_market_phase,
    )


GENERATOR_SCHEMA = "inventory-v1"
DEFAULT_SEED = 3_960_000
SOURCE_STEPS = (0, 23, 24, 718)
OPERATIONS = ("PASS", "PICKUP", "DROP", "PLACE")
COMPARED_FIELDS = (
    "after_units.market",
    "after_units.units",
    "after_town.market",
    "after_town.units",
    "after_day_end.market",
    "after_day_end.units",
    "dictionary_insertion_order",
    "exception_type_and_phase",
)


@dataclass(frozen=True, slots=True)
class ValidationCase:
    identifier: str
    layer: str
    state: InventoryState
    unit_actions: tuple[tuple[object, object], tuple[object, object]]
    shed_adjacency: tuple[tuple[bool, ...], tuple[bool, ...]]
    market_queues: tuple[object, object]
    day_end: bool = False


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


def market_state(
    source_step=1,
    players=None,
    inventory=None,
    shops=(),
    config=MarketConfig(),
):
    if inventory is None:
        inventory = {
            item: int(param.I0)
            for item, param in zip(PRODUCTS, DEFAULT_MARKET_PARAMS)
        }
    return MarketState.from_mappings(
        source_step,
        inventory,
        players or (account(), account()),
        shops,
        DEFAULT_MARKET_PARAMS,
        config,
    )


def inventory_state(
    source_step=1,
    players=None,
    units=None,
    inventory=None,
    shops=(),
    config=MarketConfig(),
):
    market = market_state(source_step, players, inventory, shops, config)
    if units is None:
        units = tuple(
            tuple(UnitInventory() for _ in range(player.hands + 1))
            for player in market.players
        )
    return InventoryState(market, units)


def _with_player(state, player, account_value, inventories):
    accounts = list(state.market.players)
    accounts[player] = account_value
    units = list(state.units)
    units[player] = inventories
    market = replace(state.market, players=tuple(accounts))
    return InventoryState(market, tuple(units))


def _with_step(state, source_step):
    return replace(state, market=replace(state.market, source_step=source_step))


def _with_config(state, config):
    return replace(state, market=replace(state.market, config=config))


def _inject(environment, case):
    environment.configuration.weedSpawnChance = 0.0
    environment.configuration.townShopUnlockInterval = 1000
    market_case = market_validator.ValidationCase(
        case.identifier,
        case.layer,
        case.state.market,
        case.market_queues,
    )
    reference_state = market_validator._inject(environment, market_case)
    for player in range(2):
        farm = reference_state[0].observation.farms[player]
        adjacency = case.shed_adjacency[player]
        positions = [[4, 4] if value else [0, 0] for value in adjacency]
        farm["farmer"] = positions[0]
        farm["hands"] = positions[1:]
        private = reference_state[player].observation.private
        private["inventories"] = [
            inventory.mapping() for inventory in case.state.units[player]
        ]
        farmer_action, hands_actions = case.unit_actions[player]
        reference_state[player].action = {
            "farmer": farmer_action,
            "hands": hands_actions,
            "market": case.market_queues[player],
        }
    injected = _project(reference_state, case.state.market)
    if injected != case.state:
        raise AssertionError("injected inventory state differs")
    for player in range(2):
        farm = reference_state[0].observation.farms[player]
        positions = [farm["farmer"], *farm["hands"]]
        derived = tuple(
            simulator._is_shed_adjacent(tuple(position), 10)
            for position in positions
        )
        if derived != case.shed_adjacency[player]:
            raise AssertionError("injected adjacency differs")
    return reference_state


def _project(reference_state, template):
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


def _run_unit_phase(reference_state, case):
    day = case.state.market.source_step // 24
    capacity = case.state.market.config.shed_capacity
    for player in range(2):
        farm = reference_state[0].observation.farms[player]
        private = reference_state[player].observation.private
        farmer_action, raw_hands = case.unit_actions[player]
        hands_actions = raw_hands if isinstance(raw_hands, list) else []
        simulator._apply_unit_action(
            farm,
            private,
            0,
            farmer_action,
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
                action,
                10,
                day,
                24,
                capacity,
            )


def _private_total(state):
    result = []
    for player in range(2):
        shed = sum(state.market.players[player].shed)
        carried = sum(
            quantity
            for inventory in state.units[player]
            for _, quantity in inventory.entries
        )
        result.append(shed + carried)
    return tuple(result)


def _unit_discarded(result):
    discarded = [0, 0]
    for event in result.events:
        discarded[event.player] += event.discarded
    return tuple(discarded)


def _day_end_discarded(result):
    return tuple(sum(vector) for vector in result.discarded)


def _check_model_invariants(case, result):
    before = _private_total(case.state)
    after_units = _private_total(result.after_units)
    discarded = _unit_discarded(result)
    if before != tuple(after_units[index] + discarded[index] for index in range(2)):
        raise AssertionError("unit item balance differs")
    direct_market = apply_market_phase(
        result.after_units.market,
        case.market_queues,
        trace=True,
    )
    if direct_market != result.market_transition:
        raise AssertionError("A1a market composition differs")
    untraced = apply_inventory_phase(
        case.state,
        case.unit_actions,
        case.shed_adjacency,
        case.market_queues,
        trace=False,
    )
    if (
        untraced.after_units != result.after_units
        or untraced.after_town != result.after_town
        or untraced.market_transition.after_town
        != result.market_transition.after_town
    ):
        raise AssertionError("trace changes inventory state")
    if untraced.events:
        raise AssertionError("untraced inventory events are not empty")


def _mismatch(case, phase, expected, actual, trace=None):
    return {
        "case": _typed(case),
        "phase": phase,
        "expected": _typed(expected),
        "actual": _typed(actual),
        "trace": _typed(trace),
    }


def compare_case(environment, case):
    model_result = None
    model_error = None
    try:
        model_result = apply_inventory_phase(
            case.state,
            case.unit_actions,
            case.shed_adjacency,
            case.market_queues,
            trace=True,
        )
    except Exception as error:
        model_error = error
    reference_state = _inject(environment, case)
    simulator_error = None
    try:
        _run_unit_phase(reference_state, case)
    except Exception as error:
        simulator_error = error
    if model_error is not None or simulator_error is not None:
        if (
            model_error is not None
            and simulator_error is not None
            and type(model_error) is type(simulator_error)
        ):
            return None, 1
        return (
            _mismatch(
                case,
                "unit_exception",
                None
                if model_error is None
                else {"type": type(model_error).__name__, "text": str(model_error)},
                None
                if simulator_error is None
                else {
                    "type": type(simulator_error).__name__,
                    "text": str(simulator_error),
                },
            ),
            0,
        )
    _check_model_invariants(case, model_result)
    actual_after_units = _project(reference_state, case.state.market)
    if actual_after_units != model_result.after_units:
        return (
            _mismatch(
                case,
                "after_units",
                model_result.after_units,
                actual_after_units,
                model_result,
            ),
            0,
        )
    simulator._process_market(reference_state, environment)
    simulator._town_consume(
        environment,
        reference_state,
        case.state.market.source_step,
    )
    actual_after_town = _project(reference_state, case.state.market)
    if actual_after_town != model_result.after_town:
        return (
            _mismatch(
                case,
                "after_town",
                model_result.after_town,
                actual_after_town,
                model_result,
            ),
            0,
        )
    if not case.day_end:
        return None, 0
    expected_day_end = apply_inventory_day_end(model_result.after_town, trace=True)
    before = _private_total(model_result.after_town)
    after = _private_total(expected_day_end.state)
    discarded = _day_end_discarded(expected_day_end)
    if before != tuple(after[index] + discarded[index] for index in range(2)):
        raise AssertionError("day-end item balance differs")
    simulator._end_of_day(
        reference_state,
        environment,
        case.state.market.source_step // 24,
    )
    actual_day_end = _project(reference_state, case.state.market)
    if actual_day_end != expected_day_end.state:
        return (
            _mismatch(
                case,
                "after_day_end",
                expected_day_end.state,
                actual_day_end,
                expected_day_end,
            ),
            0,
        )
    return None, 0


def _base_with_hands(capacity=100, source_step=1):
    config = MarketConfig(shed_capacity=capacity)
    players = (
        account(hands=1),
        account(hands=1),
    )
    units = (
        (UnitInventory(), UnitInventory()),
        (UnitInventory(), UnitInventory()),
    )
    return inventory_state(source_step, players, units, config=config)


def _set_shed(account_value, values):
    shed = _zero_mapping(SHED_ITEMS)
    shed.update(values)
    return replace(
        account_value,
        shed=tuple(shed[item] for item in SHED_ITEMS),
    )


def _boundary_cases():
    cases = []
    serial = 0

    def add_case(
        name,
        state,
        unit_actions,
        adjacency=((True, True), (True, True)),
        market_queues=([], []),
        day_end=False,
    ):
        nonlocal serial
        cases.append(
            ValidationCase(
                f"B{serial:05d}-{name}",
                "boundary",
                state,
                unit_actions,
                adjacency,
                market_queues,
                day_end,
            )
        )
        serial += 1

    base = _base_with_hands()
    passes = ((["PASS"], [["PASS"]]), (["PASS"], [["PASS"]]))
    add_case("pass", base, passes)
    add_case("non-list-hands", base, ((["PASS"], "bad"), passes[1]))
    add_case(
        "missing-and-excess-hands",
        base,
        ((["PASS"], []), (["PASS"], [["PASS"], ["DROP"]])),
    )
    for player in range(2):
        for unit_index in range(2):
            for item in SHED_ITEMS:
                player_account = _set_shed(base.market.players[player], {item: 3})
                units = list(base.units[player])
                units[unit_index] = UnitInventory(((item, 2),))
                current = _with_player(base, player, player_account, tuple(units))
                actions = [["PASS"], [["PASS"]]]
                if unit_index == 0:
                    actions[0] = ["PICKUP", item, 2]
                else:
                    actions[1] = [["PICKUP", item, 2]]
                both = [list(passes[0]), list(passes[1])]
                both[player] = actions
                add_case(
                    f"pickup-{player}-{unit_index}-{item}",
                    current,
                    (tuple(both[0]), tuple(both[1])),
                )
                if unit_index == 0:
                    actions[0] = ["PLACE", item, 2]
                else:
                    actions[1] = [["PLACE", item, 2]]
                add_case(
                    f"place-{player}-{unit_index}-{item}",
                    current,
                    (tuple(both[0]), tuple(both[1])),
                )
    quantity_cases = (
        None,
        0,
        -1,
        True,
        2.9,
        "2",
        "bad",
        100,
    )
    quantity_base = _base_with_hands()
    first = _set_shed(quantity_base.market.players[0], {"WHEAT": 5})
    quantity_base = _with_player(
        quantity_base,
        0,
        first,
        (UnitInventory((("WHEAT", 5),)), UnitInventory()),
    )
    for operation in ("PICKUP", "PLACE"):
        for quantity in quantity_cases:
            action = [operation, "WHEAT"]
            if quantity is not None:
                action.append(quantity)
            add_case(
                f"quantity-{operation}-{quantity}",
                quantity_base,
                ((action, []), passes[1]),
            )
    for action in (
        ["PICKUP"],
        ["PLACE"],
        ["PICKUP", "UNKNOWN", 1],
        ["PLACE", "UNKNOWN", 1],
        ["PICKUP", [], 1],
        ["PLACE", [], 1],
        [[]],
        (),
        [],
        "DROP",
        ["WATER"],
    ):
        add_case(
            f"raw-{len(cases)}",
            quantity_base,
            ((action, []), passes[1]),
        )
    for operation in ("PICKUP", "DROP", "PLACE"):
        add_case(
            f"nonadjacent-{operation}",
            quantity_base,
            (([operation, "WHEAT", "bad"], []), passes[1]),
            ((False, True), (True, True)),
        )
    add_case(
        "nonadjacent-place-unhashable",
        quantity_base,
        ((["PLACE", [], 1], []), passes[1]),
        ((False, True), (True, True)),
    )
    ordered = UnitInventory((("CARROT", 2), ("WHEAT", 2), ("MILK", 2)))
    for capacity in (1, 100):
        for occupancy in (0, max(0, capacity - 1), capacity, capacity + 1):
            config = MarketConfig(shed_capacity=capacity)
            current = _with_config(quantity_base, config)
            first = _set_shed(current.market.players[0], {"STRAWBERRY": occupancy})
            current = _with_player(
                current,
                0,
                first,
                (ordered, UnitInventory()),
            )
            for operation in ("DROP", "PLACE"):
                action = [operation]
                if operation == "PLACE":
                    action = [operation, "CARROT", 3]
                add_case(
                    f"capacity-{capacity}-{occupancy}-{operation}",
                    current,
                    ((action, []), passes[1]),
                )
    competition = _base_with_hands(capacity=3)
    first = _set_shed(competition.market.players[0], {"WHEAT": 2})
    competition = _with_player(
        competition,
        0,
        first,
        (
            UnitInventory((("CARROT", 2),)),
            UnitInventory((("MILK", 2),)),
        ),
    )
    add_case(
        "two-units-final-capacity",
        competition,
        ((["DROP"], [["DROP"]]), passes[1]),
    )
    add_case(
        "two-units-stock",
        competition,
        ((["PICKUP", "WHEAT", 2], [["PICKUP", "WHEAT", 2]]), passes[1]),
    )
    coupling = _base_with_hands(capacity=3)
    first = _set_shed(coupling.market.players[0], {"WHEAT": 1})
    coupling = _with_player(
        coupling,
        0,
        first,
        (UnitInventory((("WHEAT", 2),)), UnitInventory()),
    )
    coupling_cases = (
        ("drop-sell", ["DROP"], [["SELL", "WHEAT", 3]]),
        ("pickup-sell", ["PICKUP", "WHEAT", 1], [["SELL", "WHEAT", 2]]),
        ("drop-buy-product", ["DROP"], [["BUY_PRODUCT", "WHEAT", 1]]),
        ("drop-buy-animal", ["DROP"], [["BUY_ANIMAL", "GOOSE", 1]]),
        ("purchase-latency", ["PICKUP", "WHEAT", 1], [["BUY_PRODUCT", "WHEAT", 1]]),
        ("animal-latency", ["PICKUP", "GOOSE", 1], [["BUY_ANIMAL", "GOOSE", 1]]),
        ("seed-latency", ["PICKUP", "CARROT", 1], [["BUY_SEED", "CARROT", 1]]),
        ("hire-latency", ["PASS"], [["HIRE"]]),
    )
    for name, action, queue in coupling_cases:
        add_case(name, coupling, ((action, []), passes[1]), ( (True, True), (True, True)), (queue, []))
    delete_readd = _with_player(
        base,
        0,
        _set_shed(base.market.players[0], {"WHEAT": 1}),
        (UnitInventory((("CARROT", 1), ("WHEAT", 1))), UnitInventory()),
    )
    add_case(
        "delete-and-readd",
        delete_readd,
        ((["PLACE", "WHEAT", 1], [["PICKUP", "WHEAT", 1]]), passes[1]),
    )
    day_end_state = _base_with_hands(source_step=23)
    day_end_state = _with_player(
        day_end_state,
        0,
        _set_shed(day_end_state.market.players[0], {"WHEAT": 99}),
        (
            UnitInventory((("CARROT", 2), ("MILK", 2))),
            UnitInventory((("WOOL", 2),)),
        ),
    )
    for queue in ([], [["HIRE"]], [["BUY_PRODUCT", "WHEAT", 1]]):
        add_case(
            f"day-end-{len(queue)}-{len(cases)}",
            day_end_state,
            passes,
            market_queues=(queue, []),
            day_end=True,
        )
    for step in SOURCE_STEPS:
        current = _with_step(base, step)
        add_case(
            f"phase-{step}",
            current,
            passes,
            day_end=step == 23,
        )
    return cases


def _stratified_case(seed, index):
    rng = random.Random(seed ^ ((index + 1) * 1_000_003))
    operation = OPERATIONS[index % len(OPERATIONS)]
    item = SHED_ITEMS[(index + index // 4) % len(SHED_ITEMS)]
    player = (index // 48) % 2
    unit_index = (index // 24) % 2
    adjacent = bool((index // 12) % 2)
    capacity = 1 if (index // 6) % 2 else 100
    source_step = SOURCE_STEPS[(index // 4) % len(SOURCE_STEPS)]
    occupancy_mode = (index // 3) % 4
    occupancy = (0, max(0, capacity - 1), capacity, capacity + 1)[occupancy_mode]
    config = MarketConfig(shed_capacity=capacity)
    players = [
        account(money=float(rng.randint(0, 5000)), hands=1),
        account(money=float(rng.randint(0, 5000)), hands=1),
    ]
    filler = SHED_ITEMS[(SHED_ITEMS.index(item) + 1) % len(SHED_ITEMS)]
    players[player] = _set_shed(players[player], {filler: occupancy})
    units = [
        [UnitInventory(), UnitInventory()],
        [UnitInventory(), UnitInventory()],
    ]
    entries = ((item, rng.randint(1, 5)), (filler, rng.randint(1, 3)))
    units[player][unit_index] = UnitInventory(entries)
    model_state = inventory_state(
        source_step,
        tuple(players),
        (tuple(units[0]), tuple(units[1])),
        config=config,
    )
    quantity = rng.choice((1, 2, 7, True, 2.9, "2", 0, -1, "bad"))
    if operation == "PASS":
        action = ["PASS"]
    elif operation == "DROP":
        action = ["DROP"]
    else:
        action = [operation, item, quantity]
    raw_actions = [(["PASS"], [["PASS"]]), (["PASS"], [["PASS"]])]
    farmer_action, hands_actions = raw_actions[player]
    if unit_index == 0:
        farmer_action = action
    else:
        hands_actions = [action]
    raw_actions[player] = (farmer_action, hands_actions)
    adjacency = [[True, True], [True, True]]
    adjacency[player][unit_index] = adjacent
    market_variant = index % 5
    if market_variant == 0:
        market_queue = []
    elif market_variant == 1:
        market_queue = [["BUY_PRODUCT", "WHEAT", 1]]
    elif market_variant == 2:
        market_queue = [["BUY_ANIMAL", "GOOSE", 1]]
    elif market_variant == 3:
        market_queue = [["HIRE"]]
    else:
        market_queue = [["SELL", "WHEAT", 1]]
    market_queues = [[], []]
    market_queues[player] = market_queue
    return ValidationCase(
        f"R{index:05d}",
        "stratified",
        model_state,
        (tuple(raw_actions[0]), tuple(raw_actions[1])),
        (tuple(adjacency[0]), tuple(adjacency[1])),
        (market_queues[0], market_queues[1]),
        source_step == 23,
    )


def _random_cases(seed, count):
    return [_stratified_case(seed, index) for index in range(count)]


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
    if type(random_cases) is not int or random_cases < 0:
        raise ValueError("random case count must be nonnegative")
    if type(seed) is not int:
        raise TypeError("seed must be an integer")
    cases = _boundary_cases() if boundaries else []
    boundary_count = len(cases)
    cases.extend(_random_cases(seed, random_cases))
    encoded_inputs = json.dumps(
        {
            "generator_schema": GENERATOR_SCHEMA,
            "seed": seed,
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
    model_path = Path(__file__).with_name("inventory_ledger.py")
    market_model_path = Path(__file__).with_name("market_ledger.py")
    return {
        "schema": 1,
        "generator_schema": GENERATOR_SCHEMA,
        "seed": seed,
        "boundary_cases": boundary_count,
        "random_cases": random_cases,
        "fixtures": len(cases),
        "processed_fixtures": processed,
        "stratified_cases": random_cases,
        "synthetic_cases": boundary_count,
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
        "market_model_sha256": _source_hash(market_model_path),
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
        random_cases=args.random_cases,
        seed=args.seed,
        boundaries=not args.skip_boundaries,
        stop_first=args.stop_first,
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
