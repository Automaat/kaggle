import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

try:
    from .market_ledger import CROPS
    from .milp_oracle import OracleInput, solve_oracle, verify_result
except ImportError:
    from market_ledger import CROPS
    from milp_oracle import OracleInput, solve_oracle, verify_result


REGISTERED_SEED = 3_980_000


def registered_input():
    days = 30
    wheat_demand = tuple(
        0
        if day < 2 or day == 29
        else 4
        if day < 6
        else 8
        if day < 10
        else 12
        for day in range(days)
    )
    return OracleInput(
        source_step=0,
        terminal_step=718,
        cash=3000.0,
        cash_reserve=400.0,
        seeds=(0,) * len(CROPS),
        goods=(0,) * len(CROPS),
        existing_plants=(),
        tile_capacity=(13,) * days,
        action_capacity=(100,) * days,
        crop_storage_capacity=(100,) * days,
        wheat_demand=wheat_demand,
        fixed_cash_flow=(0.0,) * days,
        fertilizer_stock=0,
        fertilizer_supply=(0,) * days,
        fertilizer_buy_price=(100.0,) * days,
        market_order_slots=(5,) * days,
        base_inventory=tuple((10_000,) * len(CROPS) for _ in range(days)),
        wheat_buy_price=(25.0,) * days,
        first_plant_day=0,
        terminal_return_actions=4,
        sale_unit_limit=100,
        scenario="no-future-opponent-orders-v1",
    )


def _source_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(time_limit=120.0, mip_rel_gap=0.0):
    data = registered_input()
    result = solve_oracle(data, time_limit, mip_rel_gap)
    verification_errors = verify_result(data, result)
    return {
        "schema": 1,
        "registered_seed": REGISTERED_SEED,
        "scope": "a2a-oracle-core",
        "realized_simulator_score": None,
        "ranking_gate_complete": False,
        "input": asdict(data),
        "result": asdict(result),
        "verification_errors": verification_errors,
        "model_sha256": _source_hash(Path(__file__).with_name("milp_oracle.py")),
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
    if not document["result"]["success"]:
        raise SystemExit(1)
    if document["verification_errors"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
