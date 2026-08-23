import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

try:
    from .land_hire_optimizer import (
        TILES_PER_QUADRANT,
        OptimizerInput,
        solve_optimizer,
        verify_result,
    )
    from .market_ledger import LAND_PRICES
except ImportError:
    from land_hire_optimizer import (
        TILES_PER_QUADRANT,
        OptimizerInput,
        solve_optimizer,
        verify_result,
    )
    from market_ledger import LAND_PRICES


REGISTERED_SEED = 3_990_000
PREDECESSOR = "ea73017"
MODES = ("baseline", "land-only", "hire-only", "combined")


def registered_input():
    source_step = 10 * 24 + 18
    terminal_step = 12 * 24 + 22
    steps = tuple(range(source_step, terminal_step + 1))
    fixed_cash_flow = []
    market_order_slots = []
    existing_work = []
    land_work = []
    base_capacity = []
    executor_capacity = []
    terminal_value = []
    for step in steps:
        day = step // 24
        hour = step % 24
        fixed_cash_flow.append(1500.0 if hour == 0 and day > 10 else 0.0)
        market_order_slots.append(0 if hour == 23 else 3 if hour in (0, 18) else 2)
        existing_work.append(8 if hour < 12 else 0)
        land_work.append(1 if hour < 12 else 4)
        base_capacity.append(4 if day == 10 else 1)
        executor_capacity.append(8 if hour < 20 else 5)
        terminal_value.append(30.0 if step < terminal_step else 0.0)
    return OptimizerInput(
        source_step=source_step,
        terminal_step=terminal_step,
        cash=6000.0,
        cash_reserve=800.0,
        unlocked_quadrants=1,
        hands_today=3,
        hires_today=3,
        max_hands_per_day=12,
        hire_multiplier=1,
        fixed_cash_flow=tuple(fixed_cash_flow),
        market_order_slots=tuple(market_order_slots),
        existing_work=tuple(existing_work),
        land_work_per_quadrant=tuple(land_work),
        base_work_capacity=tuple(base_capacity),
        executor_work_capacity=tuple(executor_capacity),
        terminal_value_per_work=tuple(terminal_value),
        scenario="registered-executor-capacity-v1",
    )


def _source_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(time_limit=120.0, mip_rel_gap=0.0):
    data = registered_input()
    results = tuple(
        solve_optimizer(data, mode, time_limit, mip_rel_gap)
        for mode in MODES
    )
    verification = {
        result.mode: list(verify_result(data, result))
        for result in results
    }
    directory = Path(__file__).resolve().parent
    return {
        "schema": 1,
        "registered_seed": REGISTERED_SEED,
        "scope": "stage-f-land-hiring-shadow-optimizer",
        "shadow_only": True,
        "realized_simulator_score": None,
        "score_claim": None,
        "predecessor": PREDECESSOR,
        "environment_version": "kaggle-environments==1.32.7",
        "tiles_per_quadrant": TILES_PER_QUADRANT,
        "land_prices": list(LAND_PRICES),
        "input": asdict(data),
        "arms": [asdict(result) for result in results],
        "verification_errors": verification,
        "model_sha256": _source_hash(directory / "land_hire_optimizer.py"),
        "a1a_sha256": _source_hash(directory / "market_ledger.py"),
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
    if any(not arm["success"] for arm in document["arms"]):
        raise SystemExit(1)
    if any(document["verification_errors"].values()):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
