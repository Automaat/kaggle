import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

try:
    from .space_planner import (
        AnimalIntent,
        SpaceCell,
        SpacePlannerInput,
        solve_space_plan,
        verify_result,
    )
except ImportError:
    from space_planner import (
        AnimalIntent,
        SpaceCell,
        SpacePlannerInput,
        solve_space_plan,
        verify_result,
    )


REGISTERED_SEED = 3_980_000


def _input(cells, intents, current_day=0, terminal_day=9, capacity=10, trips=0):
    days = terminal_day - current_day + 1
    capacities = (capacity,) * days if isinstance(capacity, int) else capacity
    return SpacePlannerInput(
        current_day=current_day,
        terminal_day=terminal_day,
        cells=tuple(cells),
        intents=tuple(intents),
        action_capacity=capacities,
        action_value=1.0,
        build_actions=1,
        placement_actions=1,
        daily_service_trips=trips,
    )


def registered_cases():
    goose = AnimalIntent("goose", "GOOSE", 0, 100)
    return {
        "no_intent": _input(
            [SpaceCell((4, 4), 0, "EMPTY"), SpaceCell((4, 5), 0, "EMPTY")],
            [],
        ),
        "near_shed": _input(
            [SpaceCell((0, 0), 0, "EMPTY"), SpaceCell((4, 4), 0, "EMPTY")],
            [goose],
            trips=2,
        ),
        "dig_low_value_crop": _input(
            [SpaceCell((4, 4), 0, "PLANT", "CARROT", 5, 5)],
            [goose],
        ),
        "wait_high_value_crop": _input(
            [SpaceCell((4, 4), 0, "PLANT", "STRAWBERRY", 1000, 5)],
            [goose],
        ),
        "reuse_matching_structure": _input(
            [SpaceCell((4, 4), 0, "COOP"), SpaceCell((4, 5), 0, "EMPTY")],
            [goose],
        ),
        "defer_for_action_capacity": _input(
            [SpaceCell((4, 4), 0, "EMPTY"), SpaceCell((5, 4), 0, "EMPTY")],
            [
                AnimalIntent("cow", "COW", 0, 110),
                AnimalIntent("sheep", "SHEEP", 0, 100),
            ],
            terminal_day=3,
            capacity=(2, 2, 0, 0),
        ),
        "future_central_land": _input(
            [SpaceCell((0, 0), 0, "EMPTY"), SpaceCell((5, 4), 10, "EMPTY")],
            [AnimalIntent("late_goose", "GOOSE", 10, 100)],
            terminal_day=20,
            trips=2,
        ),
        "skip_negative_late_animal": _input(
            [SpaceCell((4, 4), 0, "EMPTY")],
            [AnimalIntent("late_goose", "GOOSE", 29, 1)],
            current_day=29,
            terminal_day=29,
        ),
    }


def _source_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(time_limit=30.0, mip_rel_gap=0.0):
    cases = []
    for name, data in registered_cases().items():
        result = solve_space_plan(data, time_limit, mip_rel_gap)
        cases.append(
            {
                "name": name,
                "input": asdict(data),
                "result": asdict(result),
                "verification_errors": verify_result(data, result),
            }
        )
    gaps = [case["result"]["mip_gap"] for case in cases]
    errors = sum(len(case["verification_errors"]) for case in cases)
    return {
        "schema": 1,
        "registered_seed": REGISTERED_SEED,
        "scope": "agent2-space-planner-shadow",
        "realized_simulator_score": None,
        "live_agent_changed": False,
        "case_count": len(cases),
        "successful_cases": sum(case["result"]["success"] for case in cases),
        "maximum_mip_gap": max(gap for gap in gaps if gap is not None),
        "verification_error_count": errors,
        "total_solver_wall_seconds": sum(
            case["result"]["wall_seconds"] for case in cases
        ),
        "cases": cases,
        "model_sha256": _source_hash(Path(__file__).with_name("space_planner.py")),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--time-limit", type=float, default=30.0)
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
    if document["successful_cases"] != document["case_count"]:
        raise SystemExit(1)
    if document["verification_error_count"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
