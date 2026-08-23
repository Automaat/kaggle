import argparse
import hashlib
import json
from pathlib import Path

from economics.market_ledger import SHED_ITEMS
from economics.rolling_coordinator import canonical_sha256
from routing.offline_route_planner import (
    COLLISION_POLICY,
    RouteFailure,
    RouteProblem,
    RouteTask,
    RouteUnit,
    plan_routes,
    verify_plan,
)


COMPARATOR_COMMIT = "b74a3ea"
COMPARATOR_SHA256 = "86951703eac27253938500eac664650c1e927d1b86b26ed84be008f24739d699"


def _hash(domain, value):
    return canonical_sha256(domain, value)


def _task(
    identifier,
    position,
    action=("WATER",),
    priority=2,
    deadline=24,
    dependencies=(),
    requires=(),
    produces=(),
):
    return RouteTask(
        identifier,
        position,
        action,
        priority,
        deadline,
        dependencies,
        requires,
        produces,
        _hash("round39-16a-precondition", identifier),
        _hash("round39-16a-effect", identifier),
    )


def _problem(name, tasks, units, shed=(), capacity=100, budget=24):
    return RouteProblem(
        0,
        10,
        tuple(units),
        tuple(shed),
        capacity,
        tuple(tasks),
        budget,
        _hash("round39-16a-route-precondition", name),
    )


def registered_scenarios():
    return (
        (
            "clustered-crops",
            _problem(
                "clustered-crops",
                (
                    _task("water-a", (1, 1)),
                    _task("water-b", (2, 1)),
                    _task("water-c", (2, 2)),
                    _task("harvest-a", (1, 2), ("HARVEST",), produces=(("CARROT", 3),)),
                ),
                (RouteUnit("farmer", (4, 4)),),
            ),
            False,
        ),
        (
            "separated-regions",
            _problem(
                "separated-regions",
                (
                    _task("north-west-a", (0, 0)),
                    _task("north-west-b", (1, 0)),
                    _task("south-east-a", (9, 9)),
                    _task("south-east-b", (8, 9)),
                ),
                (
                    RouteUnit("farmer", (0, 1)),
                    RouteUnit("hand-1", (9, 8)),
                ),
            ),
            False,
        ),
        (
            "feed-fertilizer-contention",
            _problem(
                "feed-fertilizer-contention",
                (
                    _task("feed-a", (2, 3), ("FEED",), 0, requires=(("WHEAT", 1),)),
                    _task("feed-b", (7, 6), ("FEED",), 0, requires=(("WHEAT", 1),)),
                    _task(
                        "fertilize-a",
                        (2, 2),
                        ("FERTILIZE",),
                        1,
                        requires=(("FERTILIZER", 1),),
                    ),
                    _task(
                        "fertilize-b",
                        (7, 7),
                        ("FERTILIZE",),
                        1,
                        requires=(("FERTILIZER", 1),),
                    ),
                ),
                (
                    RouteUnit("farmer", (4, 4)),
                    RouteUnit("hand-1", (5, 5)),
                ),
                (("WHEAT", 2), ("FERTILIZER", 2)),
            ),
            False,
        ),
        (
            "harvest-return",
            _problem(
                "harvest-return",
                (
                    _task("milk", (2, 4), ("HARVEST",), produces=(("MILK", 3),)),
                    _task(
                        "fertilizer",
                        (3, 4),
                        ("COLLECT_FERTILIZER",),
                        produces=(("FERTILIZER", 1),),
                    ),
                    _task("wool", (7, 5), ("HARVEST",), produces=(("WOOL", 2),)),
                ),
                (
                    RouteUnit("farmer", (4, 4)),
                    RouteUnit("hand-1", (5, 5)),
                ),
            ),
            False,
        ),
        (
            "animal-placement",
            _problem(
                "animal-placement",
                (
                    _task("build", (2, 2), ("BUILD_PASTURE",), 1),
                    _task(
                        "place",
                        (2, 2),
                        ("PLACE", "COW"),
                        0,
                        dependencies=("build",),
                        requires=(("COW", 1),),
                    ),
                ),
                (
                    RouteUnit("farmer", (4, 4)),
                    RouteUnit("hand-1", (2, 2)),
                ),
                (("COW", 1),),
            ),
            False,
        ),
        (
            "shared-cell-crossing",
            _problem(
                "shared-cell-crossing",
                (
                    _task("center-a", (4, 4)),
                    _task("center-b", (4, 4)),
                    _task("west", (3, 4)),
                    _task("east", (5, 4)),
                ),
                (
                    RouteUnit("farmer", (3, 4)),
                    RouteUnit("hand-1", (5, 4)),
                ),
            ),
            False,
        ),
        (
            "urgent-infeasible",
            _problem(
                "urgent-infeasible",
                (_task("dying-crop", (0, 0), priority=0, deadline=3),),
                (RouteUnit("farmer", (4, 4)),),
                budget=8,
            ),
            True,
        ),
        (
            "fallback-thirteen",
            _problem(
                "fallback-thirteen",
                tuple(
                    _task(f"work-{index:02d}", (index % 5, index // 5))
                    for index in range(13)
                ),
                (
                    RouteUnit("farmer", (0, 0)),
                    RouteUnit("hand-1", (4, 0)),
                    RouteUnit("hand-2", (0, 2)),
                ),
            ),
            False,
        ),
    )


def _distance(first, second):
    return abs(first[0] - second[0]) + abs(first[1] - second[1])


def _shed_access(board_size):
    half = board_size // 2
    return (
        (half - 1, half - 1),
        (half, half - 1),
        (half - 1, half),
        (half, half),
    )


def _inventory(entries):
    result = [0] * len(SHED_ITEMS)
    for item, quantity in entries:
        result[SHED_ITEMS.index(item)] = quantity
    return result


def _legacy_proxy(problem):
    positions = [unit.position for unit in problem.units]
    inventories = [_inventory(unit.inventory) for unit in problem.units]
    shed = _inventory(problem.shed)
    movement = [0] * len(problem.units)
    actions = [0] * len(problem.units)
    completed = set()
    remaining = set(range(len(problem.tasks)))
    accesses = _shed_access(problem.board_size)
    while remaining:
        choices = []
        for unit_index, position in enumerate(positions):
            for task_index in sorted(remaining):
                task = problem.tasks[task_index]
                if any(dependency not in completed for dependency in task.dependencies):
                    continue
                distance = _distance(position, task.position)
                route_class = 0 if task.priority == 0 else 1 if distance <= 2 else 2
                choices.append(
                    (
                        route_class,
                        task.priority,
                        distance,
                        unit_index,
                        task.identifier,
                        task_index,
                    )
                )
        if not choices:
            return None
        _route_class, _priority, _distance_value, unit_index, _task_id, task_index = min(choices)
        task = problem.tasks[task_index]
        for item, quantity in task.requires:
            item_index = SHED_ITEMS.index(item)
            shortage = max(0, quantity - inventories[unit_index][item_index])
            if shortage:
                if shed[item_index] < shortage:
                    return None
                access = min(
                    accesses,
                    key=lambda value: (_distance(positions[unit_index], value), accesses.index(value)),
                )
                movement[unit_index] += _distance(positions[unit_index], access)
                positions[unit_index] = access
                actions[unit_index] += 1
                shed[item_index] -= shortage
                inventories[unit_index][item_index] += shortage
        movement[unit_index] += _distance(positions[unit_index], task.position)
        positions[unit_index] = task.position
        actions[unit_index] += 1
        for item, quantity in task.requires:
            inventories[unit_index][SHED_ITEMS.index(item)] -= quantity
        for item, quantity in task.produces:
            inventories[unit_index][SHED_ITEMS.index(item)] += quantity
        remaining.remove(task_index)
        completed.add(task.identifier)
    for unit_index, inventory in enumerate(inventories):
        if not any(inventory):
            continue
        access = min(
            accesses,
            key=lambda value: (_distance(positions[unit_index], value), accesses.index(value)),
        )
        movement[unit_index] += _distance(positions[unit_index], access)
        positions[unit_index] = access
        actions[unit_index] += 1
    loads = tuple(left + right for left, right in zip(movement, actions, strict=True))
    return {
        "cost": sum(loads),
        "movement": sum(movement),
        "actions": sum(actions),
        "maximum_unit_load": max(loads, default=0),
        "within_command_budget": all(value <= problem.max_commands_per_unit for value in loads),
    }


def _source_sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_result():
    scenario_results = []
    successes = []
    for name, problem, expected_failure in registered_scenarios():
        result = plan_routes(problem)
        if isinstance(result, RouteFailure):
            scenario_results.append(
                {
                    "name": name,
                    "expected_failure": expected_failure,
                    "outcome": "failure",
                    "phase": result.phase,
                    "message": result.message,
                    "problem_fingerprint": result.problem_fingerprint,
                }
            )
            continue
        errors = verify_plan(problem, result)
        proxy = _legacy_proxy(problem)
        row = {
            "name": name,
            "expected_failure": expected_failure,
            "outcome": "plan",
            "optimal": result.optimal,
            "collision_policy": result.collision_policy,
            "tasks": len(problem.tasks),
            "units": len(problem.units),
            "cost": result.total_cost,
            "movement": result.total_movement,
            "actions": result.total_actions,
            "maximum_unit_load": result.maximum_unit_load,
            "verification_errors": list(errors),
            "plan_fingerprint": result.fingerprint,
            "legacy_1_14_proxy": proxy,
            "proxy_cost_delta": result.total_cost - proxy["cost"] if proxy else None,
        }
        scenario_results.append(row)
        successes.append(row)
    unexpected = [
        row["name"]
        for row in scenario_results
        if (row["outcome"] == "failure") != row["expected_failure"]
    ]
    verification_errors = sum(
        len(row.get("verification_errors", ()))
        for row in scenario_results
    )
    proxy_rows = [row for row in successes if row["legacy_1_14_proxy"] is not None]
    result = {
        "schema": "agent2-offline-routes-v1",
        "experiment": "39.16A",
        "status": "accepted-standalone",
        "comparator": {
            "name": "1.14.0 route-choice proxy",
            "commit": COMPARATOR_COMMIT,
            "sha256": COMPARATOR_SHA256,
            "action_equivalent": False,
        },
        "source_step_range": [0, 718],
        "game_days": 30,
        "exact_task_limit": 12,
        "collision_policy": COLLISION_POLICY,
        "scenarios": scenario_results,
        "aggregate": {
            "registered_scenarios": len(scenario_results),
            "successful_plans": len(successes),
            "expected_failures": sum(row["expected_failure"] for row in scenario_results),
            "unexpected_outcomes": unexpected,
            "verification_errors": verification_errors,
            "exact_plans": sum(row["optimal"] for row in successes),
            "fallback_plans": sum(not row["optimal"] for row in successes),
            "route_cost": sum(row["cost"] for row in proxy_rows),
            "route_movement": sum(row["movement"] for row in proxy_rows),
            "route_actions": sum(row["actions"] for row in proxy_rows),
            "legacy_1_14_proxy_cost": sum(
                row["legacy_1_14_proxy"]["cost"] for row in proxy_rows
            ),
            "proxy_cost_delta": sum(row["proxy_cost_delta"] for row in proxy_rows),
        },
        "limitations": [
            "standalone task input; no live observation adapter",
            "the 1.14 comparator is a documented route-choice proxy, not action equivalence",
            "produced items do not satisfy later same-route requirements",
            "shed capacity reserves all route returns before any planned pickup",
            "the deterministic fallback is not globally optimal",
            "no full-game score or replay was produced",
        ],
        "source_sha256": {
            "planner": _source_sha256(Path(__file__).with_name("offline_route_planner.py")),
            "runner": _source_sha256(Path(__file__)),
        },
    }
    hash_input = dict(result)
    result["result_sha256"] = canonical_sha256("round39-16a-result", hash_input)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    result = build_result()
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if arguments.output is not None:
        arguments.output.write_text(text)
    print(text, end="")
    if result["aggregate"]["unexpected_outcomes"] or result["aggregate"]["verification_errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
