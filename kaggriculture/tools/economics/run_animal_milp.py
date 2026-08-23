import argparse
import hashlib
import json
from dataclasses import asdict, replace
from itertools import combinations_with_replacement
from pathlib import Path

try:
    from .animal_milp import (
        ANIMALS,
        GOODS,
        AnimalOracleInput,
        solve_animal_oracle,
        verify_result,
    )
except ImportError:
    from animal_milp import (
        ANIMALS,
        GOODS,
        AnimalOracleInput,
        solve_animal_oracle,
        verify_result,
    )


REGISTERED_SEED = 3_980_000


def registered_input(max_new_animals=4, allowed_animals=ANIMALS):
    days = 30
    return AnimalOracleInput(
        source_step=0,
        terminal_step=718,
        cash=3000.0,
        cash_reserve=400.0,
        goods=(0,) * len(GOODS),
        shed_animals=(0,) * len(ANIMALS),
        existing_animals=(),
        empty_structures=(0, 0),
        animal_tile_capacity=(12,) * days,
        action_capacity=(60,) * days,
        shed_capacity=(100,) * days,
        fixed_shed_occupancy=(0,) * days,
        market_order_slots=(10,) * days,
        fixed_cash_flow=(0.0,) * days,
        base_inventory=tuple((10_000,) * len(GOODS) for _ in range(days)),
        wheat_buy_unit_limit=120,
        placement_travel_actions=4,
        feed_actions_per_unit=2,
        return_actions=4,
        sale_unit_limit=40,
        max_new_animals=max_new_animals,
        allowed_animals=tuple(allowed_animals),
        fixed_slot_animals=(),
        scenario="no-future-opponent-orders-v1",
    )


def registered_cases():
    base = registered_input()
    return (
        ("no_purchase", replace(base, max_new_animals=0)),
        (
            "one_goose",
            replace(
                base,
                max_new_animals=1,
                allowed_animals=("GOOSE",),
                fixed_slot_animals=("GOOSE",),
            ),
        ),
        (
            "one_cow",
            replace(
                base,
                max_new_animals=1,
                allowed_animals=("COW",),
                fixed_slot_animals=("COW",),
            ),
        ),
        (
            "one_sheep",
            replace(
                base,
                max_new_animals=1,
                allowed_animals=("SHEEP",),
                fixed_slot_animals=("SHEEP",),
            ),
        ),
    )


def _source_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(time_limit=120.0, mip_rel_gap=0.0):
    cases = []
    portfolio_candidates = []
    baseline_cash = None
    for name, data in registered_cases():
        result = solve_animal_oracle(data, time_limit, mip_rel_gap)
        verification_errors = verify_result(data, result)
        if name == "no_purchase" and result.success:
            baseline_cash = result.terminal_cash
        delta = None
        if baseline_cash is not None and result.terminal_cash is not None:
            delta = result.terminal_cash - baseline_cash
        cases.append(
            {
                "name": name,
                "input": asdict(data),
                "result": asdict(result),
                "delta_vs_no_purchase": delta,
                "verification_errors": verification_errors,
            }
        )
    portfolio_results = []
    for composition in combinations_with_replacement(ANIMALS, 4):
        data = replace(
            registered_input(),
            fixed_slot_animals=composition,
        )
        result = solve_animal_oracle(data, time_limit, mip_rel_gap)
        verification_errors = verify_result(data, result)
        portfolio_results.append((composition, data, result, verification_errors))
        portfolio_candidates.append(
            {
                "composition": composition,
                "success": result.success,
                "status": result.status,
                "mip_gap": result.mip_gap,
                "wall_seconds": result.wall_seconds,
                "terminal_cash": result.terminal_cash,
                "selected_animals": tuple(
                    animal.animal for animal in result.animals if not animal.existing
                ),
                "verification_errors": verification_errors,
            }
        )
    valid = [
        value
        for value in portfolio_results
        if value[2].success and not value[3]
    ]
    if valid:
        composition, data, result, verification_errors = max(
            valid,
            key=lambda value: value[2].terminal_cash,
        )
        delta = None
        if baseline_cash is not None:
            delta = result.terminal_cash - baseline_cash
        cases.append(
            {
                "name": "portfolio_four",
                "enumerated_composition": composition,
                "input": asdict(data),
                "result": asdict(result),
                "delta_vs_no_purchase": delta,
                "verification_errors": verification_errors,
            }
        )
    return {
        "schema": 1,
        "registered_seed": REGISTERED_SEED,
        "scope": "d-animal-portfolio-shadow",
        "realized_simulator_score": None,
        "live_control": False,
        "cases": cases,
        "portfolio_candidates": portfolio_candidates,
        "model_sha256": _source_hash(Path(__file__).with_name("animal_milp.py")),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--time-limit", type=float, default=120.0)
    parser.add_argument("--mip-rel-gap", type=float, default=0.0)
    parser.add_argument("--output")
    args = parser.parse_args()
    document = run(args.time_limit, args.mip_rel_gap)
    encoded = json.dumps(document, indent=2, sort_keys=True)
    print(encoded)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded + "\n")
    if any(not case["result"]["success"] for case in document["cases"]):
        raise SystemExit(1)
    if any(not case["success"] for case in document["portfolio_candidates"]):
        raise SystemExit(1)
    if any(case["verification_errors"] for case in document["cases"]):
        raise SystemExit(2)
    if any(case["verification_errors"] for case in document["portfolio_candidates"]):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
