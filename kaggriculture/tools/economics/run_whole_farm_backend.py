import argparse
import hashlib
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

try:
    from .land_hire_optimizer import OptimizerInput
    from .rolling_coordinator import (
        ExecutionSignal,
        PlanFailure,
        RollingCoordinator,
        RollingObservation,
        canonical_sha256,
    )
    from .run_animal_milp import registered_input as registered_animal_input
    from .run_milp_oracle import registered_input as registered_crop_input
    from .space_planner import SpaceCell
    from .whole_farm_backend import (
        REGISTERED_SEED,
        SOURCE_COMMITS,
        SharedCapacity,
        WholeFarmPlannerBackend,
        WholeFarmSnapshot,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.economics.land_hire_optimizer import OptimizerInput
    from tools.economics.rolling_coordinator import (
        ExecutionSignal,
        PlanFailure,
        RollingCoordinator,
        RollingObservation,
        canonical_sha256,
    )
    from tools.economics.run_animal_milp import (
        registered_input as registered_animal_input,
    )
    from tools.economics.run_milp_oracle import registered_input as registered_crop_input
    from tools.economics.space_planner import SpaceCell
    from tools.economics.whole_farm_backend import (
        REGISTERED_SEED,
        SOURCE_COMMITS,
        SharedCapacity,
        WholeFarmPlannerBackend,
        WholeFarmSnapshot,
    )


COMPARATOR_COMMIT = "b74a3ea"
COMPARATOR_SHA256 = "86951703eac27253938500eac664650c1e927d1b86b26ed84be008f24739d699"


def _registered_investment_input():
    source_step = 0
    terminal_step = 718
    steps = tuple(range(source_step, terminal_step + 1))
    return OptimizerInput(
        source_step,
        terminal_step,
        3000.0,
        400.0,
        1,
        0,
        0,
        12,
        1,
        (0.0,) * len(steps),
        (3,) * len(steps),
        tuple(6 if step % 24 < 12 else 0 for step in steps),
        tuple(1 if step % 24 < 12 else 0 for step in steps),
        (4,) * len(steps),
        (12,) * len(steps),
        tuple(4.0 if step % 24 < 12 else 0.0 for step in steps),
        "registered-executor-capacity-v1",
    )


def _registered_cells():
    cells = []
    for row in range(10):
        for column in range(10):
            quadrant = 0
            if row >= 5:
                quadrant += 2
            if column >= 5:
                quadrant += 1
            cells.append(SpaceCell((row, column), quadrant * 5, "EMPTY"))
    return tuple(cells)


def registered_snapshot():
    days = 30
    crop = replace(
        registered_crop_input(),
        wheat_demand=(0,) * days,
        tile_capacity=(25,) * days,
        action_capacity=(100,) * days,
        crop_storage_capacity=(100,) * days,
        market_order_slots=(10,) * days,
    )
    animal = replace(
        registered_animal_input(),
        animal_tile_capacity=(25,) * days,
        action_capacity=(100,) * days,
        market_order_slots=(10,) * days,
    )
    shared = SharedCapacity(
        (25,) * days,
        (100,) * days,
        (100,) * days,
        (10,) * days,
        (12,) * days,
    )
    return WholeFarmSnapshot(
        REGISTERED_SEED,
        crop,
        animal,
        _registered_investment_input(),
        _registered_cells(),
        shared,
        tuple(("SHEEP",) * count for count in range(4, -1, -1)),
    )


def registered_observation():
    return RollingObservation(
        0,
        (),
        canonical_sha256("registered-economy", REGISTERED_SEED),
        canonical_sha256("registered-topology", REGISTERED_SEED),
        canonical_sha256("registered-route", REGISTERED_SEED),
        canonical_sha256("registered-progress", REGISTERED_SEED),
        ExecutionSignal(),
    )


def _source_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(time_limit=30.0, mip_rel_gap=0.0):
    snapshot = registered_snapshot()
    backend = WholeFarmPlannerBackend(
        lambda observation: snapshot,
        time_limit,
        mip_rel_gap,
        5,
    )
    coordinator = RollingCoordinator(backend)
    intent = coordinator.prepare(registered_observation())
    if isinstance(intent, PlanFailure):
        return {
            "schema": 1,
            "registered_seed": REGISTERED_SEED,
            "scope": "agent2-whole-farm-shadow",
            "success": False,
            "failure": asdict(intent),
        }
    solved = backend.last_solve
    handoff = backend.last_handoff
    trace = backend.last_trace
    if solved is None or handoff is None or trace is None:
        raise RuntimeError("backend did not retain its solve")
    return {
        "schema": 1,
        "registered_seed": REGISTERED_SEED,
        "scope": "agent2-whole-farm-shadow",
        "execution_label": "strategy-2.0-execution-1.14",
        "success": not solved.verification.errors,
        "shadow_only": True,
        "realized_simulator_score": None,
        "game_played": False,
        "comparator": {
            "commit": COMPARATOR_COMMIT,
            "sha256": COMPARATOR_SHA256,
        },
        "source_commits": dict(SOURCE_COMMITS),
        "intent": asdict(intent),
        "selected_investment": {
            "mode": solved.selected_investment.mode,
            "investment_cost": solved.selected_investment.investment_cost,
            "incremental_terminal_cash": solved.selected_investment.incremental_terminal_cash,
            "decisions": [asdict(value) for value in solved.selected_investment.investments],
        },
        "animal": {
            "terminal_cash_standalone": solved.animal_result.terminal_cash,
            "incremental_profit_standalone": solved.animal_result.incremental_animal_profit,
            "selected": [
                asdict(value)
                for value in solved.animal_result.animals
                if not value.existing
            ],
        },
        "crop": {
            "shared_terminal_cash": solved.crop_result.terminal_cash,
            "incremental_profit": solved.crop_result.incremental_crop_profit,
            "decision_count": len(solved.crop_result.decisions),
        },
        "space": {
            "assignments": [asdict(value) for value in solved.space_result.assignments],
            "rejected_intents": solved.space_result.rejected_intents,
        },
        "execution_handoff": asdict(handoff),
        "decision_trace": asdict(trace),
        "verification_errors": solved.verification.errors,
        "model_sha256": _source_hash(Path(__file__).with_name("whole_farm_backend.py")),
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
    if not document["success"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
