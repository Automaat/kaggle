import argparse
import hashlib
import importlib.metadata
import inspect
import json
import random
import time
from dataclasses import asdict, dataclass, fields, is_dataclass, replace
from pathlib import Path

from kaggle_environments import make
from kaggle_environments.envs.kaggriculture import kaggriculture as simulator

try:
    from .market_ledger import (
        ANIMALS,
        CROPS,
        DEFAULT_MARKET_PARAMS,
        MARKET_LOOP_LIMIT,
        PRODUCTS,
        SHED_ITEMS,
        SHOP_DEMAND,
        MarketConfig,
        MarketState,
        PlayerAccount,
        apply_market_phase,
        resolve_market_params,
    )
except ImportError:
    from market_ledger import (
        ANIMALS,
        CROPS,
        DEFAULT_MARKET_PARAMS,
        MARKET_LOOP_LIMIT,
        PRODUCTS,
        SHED_ITEMS,
        SHOP_DEMAND,
        MarketConfig,
        MarketState,
        PlayerAccount,
        apply_market_phase,
        resolve_market_params,
    )


COMPARED_FIELDS = (
    "source_step",
    "market.inventory",
    "market.prices",
    "players.money",
    "players.shed",
    "players.seeds",
    "players.hires_today",
    "players.hand_count",
    "players.unlocked_quadrant_count",
)
LAND_ORDER = ("NE", "SW", "SE")


@dataclass(frozen=True, slots=True)
class ValidationCase:
    identifier: str
    layer: str
    state: MarketState
    queues: tuple[object, object]


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


def state(
    source_step=1,
    inventory=None,
    players=None,
    shops=(),
    params=DEFAULT_MARKET_PARAMS,
    config=MarketConfig(),
):
    if inventory is None:
        inventory = {
            item: int(params[index].I0)
            for index, item in enumerate(PRODUCTS)
        }
    return MarketState.from_mappings(
        source_step,
        inventory,
        players or (account(), account()),
        shops,
        params,
        config,
    )


def _params_mapping(params):
    return {
        item: asdict(param)
        for item, param in zip(PRODUCTS, params)
    }


def _unlock_tiles(farm, count, board_size):
    unlocked = ["NW", *LAND_ORDER[: count - 1]]
    farm["unlocked_quadrants"] = unlocked
    farm["tiles"] = [
        [
            None
            if simulator._quadrant_of(x, y, board_size) in unlocked
            else "LOCKED"
            for x in range(board_size)
        ]
        for y in range(board_size)
    ]


def _inject(environment, case):
    config = case.state.config
    environment.configuration.boardSize = 10
    environment.configuration.maxMarketOrdersPerTurn = config.max_orders
    environment.configuration.shedCapacity = config.shed_capacity
    environment.configuration.farmHandCostMult = config.hire_multiplier
    environment.configuration.townShopSellInterval = config.shop_interval
    environment.configuration.townCenterSellInterval = config.center_interval
    environment.reset(2)
    reference_state = environment.state
    shared = reference_state[0].observation
    shared.step = case.state.source_step
    shared.day = case.state.source_step // 24
    shared.hour = case.state.source_step % 24
    shared.market["inventory"] = case.state.inventory_mapping()
    shared.market["params"] = _params_mapping(case.state.params)
    shared.market["prices"] = dict(zip(PRODUCTS, case.state.prices))
    shared.town["unlocked_shops"] = list(case.state.shops)
    for player_id, model_account in enumerate(case.state.players):
        farm = shared.farms[player_id]
        farm["money"] = model_account.money
        farm["hires_today"] = model_account.hires_today
        farm["farmer"] = [4, 4]
        farm["hands"] = [[4, 4] for _ in range(model_account.hands)]
        _unlock_tiles(farm, model_account.unlocked_quadrants, 10)
        private = reference_state[player_id].observation.private
        private["shed"] = model_account.shed_mapping()
        private["seeds"] = model_account.seed_mapping()
        private["inventories"] = [
            {} for _ in range(model_account.hands + 1)
        ]
        reference_state[player_id].observation.step = case.state.source_step
        reference_state[player_id].observation.day = case.state.source_step // 24
        reference_state[player_id].observation.hour = case.state.source_step % 24
        reference_state[player_id].observation.farms = shared.farms
        reference_state[player_id].observation.market = shared.market
        reference_state[player_id].observation.town = shared.town
        reference_state[player_id].action = {
            "farmer": ["PASS"],
            "hands": [["PASS"] for _ in range(model_account.hands)],
            "market": case.queues[player_id],
        }
    return reference_state


def _project(reference_state, model_state):
    shared = reference_state[0].observation
    players = []
    for player_id in range(2):
        farm = shared.farms[player_id]
        private = reference_state[player_id].observation.private
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
    projected = MarketState.from_mappings(
        model_state.source_step,
        {item: shared.market["inventory"][item] for item in PRODUCTS},
        tuple(players),
        tuple(shared.town["unlocked_shops"]),
        model_state.params,
        model_state.config,
    )
    prices = tuple(shared.market["prices"][item] for item in PRODUCTS)
    return projected, prices


def compare_case(environment, case):
    reference_state = _inject(environment, case)
    injected, injected_prices = _project(reference_state, case.state)
    if injected != case.state or injected_prices != case.state.prices:
        raise AssertionError("injected state differs")
    model_result = apply_market_phase(case.state, case.queues)
    simulator._process_market(reference_state, environment)
    simulator._town_consume(environment, reference_state, case.state.source_step)
    actual, actual_prices = _project(reference_state, case.state)
    expected = model_result.after_town
    if actual == expected and actual_prices == expected.prices:
        return None
    traced = apply_market_phase(case.state, case.queues, trace=True)
    return {
        "case": _typed(case),
        "expected": _typed(expected),
        "expected_prices": list(expected.prices),
        "actual": _typed(actual),
        "actual_prices": list(actual_prices),
        "trace": _typed(traced),
    }


def _replace_player(model_state, player_id, new_account):
    players = list(model_state.players)
    players[player_id] = new_account
    return replace(model_state, players=tuple(players))


def _with_shed(model_account, item, quantity):
    shed = model_account.shed_mapping()
    shed[item] = quantity
    return replace(model_account, shed=tuple(shed[name] for name in SHED_ITEMS))


def _boundary_cases():
    cases = []
    identifier = 0

    def add(model_state, queues, name):
        nonlocal identifier
        cases.append(
            ValidationCase(
                f"B{identifier:05d}-{name}",
                "boundary",
                model_state,
                queues,
            )
        )
        identifier += 1

    base = state()
    for seat in range(2):
        other = 1 - seat
        for item in PRODUCTS:
            for quantity in range(5):
                player = _with_shed(base.players[seat], item, max(1, quantity))
                current = _replace_player(base, seat, player)
                queues = [[], []]
                queues[seat] = [["SELL", item, quantity]]
                add(current, tuple(queues), f"sell-{seat}-{item}-{quantity}")
        fixed_operations = (
            ("BUY_PRODUCT", ("WHEAT", "FERTILIZER"), {"WHEAT": 25, "FERTILIZER": 100}),
            ("BUY_SEED", CROPS, simulator.CROPS),
            ("BUY_ANIMAL", ANIMALS, simulator.ANIMALS),
        )
        for operation, items, costs in fixed_operations:
            for item in items:
                raw_cost = costs[item]
                cost = raw_cost if isinstance(raw_cost, int) else raw_cost.get("seed", raw_cost.get("cost"))
                for quantity in range(5):
                    for money in (0, max(0, cost - 1), cost, cost + 1, cost * 4):
                        player = replace(base.players[seat], money=float(money))
                        current = _replace_player(base, seat, player)
                        queues = [[], []]
                        queues[seat] = [[operation, item, quantity]]
                        add(
                            current,
                            tuple(queues),
                            f"{operation}-{seat}-{item}-{quantity}-{money}",
                        )
        for hires in range(8):
            cost = _fib(hires)
            for money in (max(0, cost - 1), cost, cost + 1):
                player = replace(
                    base.players[seat],
                    money=float(money),
                    hires_today=hires,
                    hands=hires,
                )
                current = _replace_player(base, seat, player)
                queues = [[], []]
                queues[seat] = [["HIRE"]]
                add(current, tuple(queues), f"hire-{seat}-{hires}-{money}")
        for unlocked in range(1, 5):
            cost = 4000 if unlocked == 4 else (1000, 2000, 4000)[unlocked - 1]
            for money in (max(0, cost - 1), cost, cost + 1):
                player = replace(
                    base.players[seat],
                    money=float(money),
                    unlocked_quadrants=unlocked,
                )
                current = _replace_player(base, seat, player)
                queues = [[], []]
                queues[seat] = [["BUY_LAND"]]
                add(current, tuple(queues), f"land-{seat}-{unlocked}-{money}")
        add(base, ([], []), f"empty-{other}")
    for item in PRODUCTS:
        current = base
        players = []
        for player in current.players:
            players.append(_with_shed(player, item, 4))
        current = replace(current, players=tuple(players))
        add(
            current,
            ([['SELL', item, 4]], [['SELL', item, 3]]),
            f"same-sell-{item}",
        )
    add(
        base,
        ([['BUY_PRODUCT', 'WHEAT', 4]], [['SELL', 'WHEAT', 4]]),
        "buy-sell-lockstep",
    )
    malformed = (
        (),
        [()],
        [[]],
        [["UNKNOWN"]],
        [["SELL"]],
        [["SELL", "WHEAT", True]],
        [["SELL", "WHEAT", 2.9]],
        [["SELL", "WHEAT", "2", "EXTRA"]],
    )
    for index, queue in enumerate(malformed):
        add(base, (queue, []), f"parse-{index}")
    add(
        replace(base, config=replace(base.config, max_orders=1)),
        ([['HIRE'], ['HIRE']], []),
        "order-limit-one",
    )
    add(
        base,
        ([['HIRE']] * 11, []),
        "order-limit-ten",
    )
    huge_account = _with_shed(replace(base.players[0], money=0.0), "WHEAT", MARKET_LOOP_LIMIT + 1)
    huge = _replace_player(base, 0, huge_account)
    add(huge, ([['SELL', 'WHEAT', MARKET_LOOP_LIMIT - 1]], []), "loop-limit-minus-one")
    add(huge, ([['SELL', 'WHEAT', MARKET_LOOP_LIMIT]], []), "loop-limit")
    for step in (0, 23, 24, 47, 48, 717, 718):
        add(replace(base, source_step=step), ([], []), f"town-step-{step}")
        add(
            replace(
                base,
                source_step=step,
                shops=tuple(SHOP_DEMAND),
            ),
            ([], []),
            f"all-shops-{step}",
        )
    for item, param in zip(PRODUCTS, DEFAULT_MARKET_PARAMS):
        for value in (
            int(param.I0 - param.T),
            int(param.I0),
            int(param.I0 + param.T),
            int(param.I0 + 2 * param.T),
            -1,
        ):
            inventory = base.inventory_mapping()
            inventory[item] = value
            add(
                MarketState.from_mappings(
                    1,
                    inventory,
                    base.players,
                    (),
                    base.params,
                    base.config,
                ),
                ([], []),
                f"price-{item}-{value}",
            )
    overrides = {
        "WHEAT": {"above_func": "log10", "above_target": 0.7, "I0": 9000, "T": 150},
        "WOOL": {"below_func": "unknown", "below_target": 0.5},
    }
    custom_params = resolve_market_params(overrides)
    add(state(params=custom_params), ([], []), "parameter-overrides")
    custom_config = MarketConfig(10, 100, 3, 3, 7)
    add(state(source_step=21, config=custom_config), ([], []), "custom-town-interval")
    for capacity in (1, 100):
        for occupancy in (max(0, capacity - 1), capacity, capacity + 1):
            shed = _zero_mapping(SHED_ITEMS)
            shed["WHEAT"] = occupancy
            player = account(money=1000.0, shed=shed)
            current = _replace_player(
                replace(base, config=replace(base.config, shed_capacity=capacity)),
                0,
                player,
            )
            add(
                current,
                ([['BUY_PRODUCT', 'WHEAT', 1]], []),
                f"capacity-{capacity}-{occupancy}",
            )
    return cases


def _fib(index):
    first, second = 1, 1
    for _ in range(index):
        first, second = second, first + second
    return first


def _random_account(rng, capacity):
    shed = _zero_mapping(SHED_ITEMS)
    for _ in range(rng.randint(0, min(capacity, 8))):
        shed[rng.choice(SHED_ITEMS)] += 1
    seeds = {item: rng.randint(0, 5) for item in CROPS}
    hires = rng.randint(0, 8)
    return account(
        money=float(rng.randint(0, 100_000)),
        shed=shed,
        seeds=seeds,
        hires_today=hires,
        unlocked_quadrants=rng.randint(1, 4),
        hands=hires,
    )


def _random_order(rng):
    operation = rng.choice(
        ("SELL", "BUY_PRODUCT", "BUY_SEED", "BUY_ANIMAL", "HIRE", "BUY_LAND")
    )
    if operation in ("HIRE", "BUY_LAND"):
        return [operation] if rng.random() < 0.9 else [operation, "EXTRA"]
    items = {
        "SELL": PRODUCTS,
        "BUY_PRODUCT": BUYABLE_PRODUCTS,
        "BUY_SEED": CROPS,
        "BUY_ANIMAL": ANIMALS,
    }[operation]
    quantity = rng.choice((1, 1, 1, 2, 3, 4, "2", 2.9, True))
    return [operation, rng.choice(items), quantity]


BUYABLE_PRODUCTS = ("WHEAT", "FERTILIZER")


def _random_cases(seed, count):
    rng = random.Random(seed)
    cases = []
    shops = tuple(sorted(SHOP_DEMAND))
    for index in range(count):
        capacity = rng.choice((1, 100))
        config = MarketConfig(
            rng.choice((1, 10)),
            capacity,
            rng.choice((0, 1, 3)),
            rng.choice((1, 3, 4, 7)),
            rng.choice((1, 7, 24)),
        )
        params = DEFAULT_MARKET_PARAMS
        if rng.random() < 0.1:
            item = rng.choice(PRODUCTS)
            params = resolve_market_params(
                {
                    item: {
                        "above_func": rng.choice(("linear", "sq", "sqrt", "log", "log10", "hinge")),
                        "above_target": rng.choice((0.2, 0.7, 1.6)),
                    }
                }
            )
        inventory = {
            item: int(params[position].I0) + rng.randint(-1200, 1200)
            for position, item in enumerate(PRODUCTS)
        }
        model_state = state(
            source_step=rng.randint(0, 718),
            inventory=inventory,
            players=(
                _random_account(rng, capacity),
                _random_account(rng, capacity),
            ),
            shops=tuple(rng.choice(shops) for _ in range(rng.randint(0, 8))),
            params=params,
            config=config,
        )
        queues = tuple(
            [_random_order(rng) for _ in range(rng.randint(0, 12))]
            for _ in range(2)
        )
        cases.append(
            ValidationCase(
                f"R{index:05d}",
                "reachable",
                model_state,
                queues,
            )
        )
    return cases


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


def run_validation(random_cases=10_000, seed=3_950_000, boundaries=True):
    if type(random_cases) is not int or random_cases < 0:
        raise ValueError("random case count must be nonnegative")
    cases = _boundary_cases() if boundaries else []
    boundary_count = len(cases)
    cases.extend(_random_cases(seed, random_cases))
    encoded_inputs = json.dumps(
        [_typed(case) for case in cases],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    environment = make("kaggriculture", configuration={"episodeSteps": 720})
    first_mismatch = None
    first_failure = None
    mismatches = 0
    failures = 0
    started = time.perf_counter()
    for case in cases:
        try:
            mismatch = compare_case(environment, case)
        except Exception as error:
            failures += 1
            if first_failure is None:
                first_failure = {
                    "case": _typed(case),
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
            continue
        if mismatch is not None:
            mismatches += 1
            if first_mismatch is None:
                first_mismatch = mismatch
    elapsed = time.perf_counter() - started
    simulator_path = inspect.getsourcefile(simulator)
    model_path = Path(__file__).with_name("market_ledger.py")
    result = {
        "schema": 1,
        "seed": seed,
        "boundary_cases": boundary_count,
        "random_cases": random_cases,
        "fixtures": len(cases),
        "reachable_cases": random_cases,
        "synthetic_cases": boundary_count,
        "compared_fields": list(COMPARED_FIELDS),
        "mismatches": mismatches,
        "failures": failures,
        "first_mismatch": first_mismatch,
        "first_failure": first_failure,
        "elapsed_seconds": elapsed,
        "environment_version": importlib.metadata.version("kaggle-environments"),
        "input_sha256": hashlib.sha256(encoded_inputs).hexdigest(),
        "model_sha256": _source_hash(model_path),
        "simulator_sha256": _source_hash(simulator_path),
    }
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--random-cases", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=3_950_000)
    parser.add_argument("--skip-boundaries", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()
    result = run_validation(
        random_cases=args.random_cases,
        seed=args.seed,
        boundaries=not args.skip_boundaries,
    )
    encoded = json.dumps(result, indent=2, sort_keys=True)
    print(encoded)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded + "\n")
    if result["mismatches"] or result["failures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
