import dataclasses
import pathlib
import sys

import pytest


sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))

from economics.rolling_coordinator import canonical_sha256
from routing.offline_route_planner import (
    COLLISION_POLICY,
    RouteExecutor,
    RouteFailure,
    RoutePlan,
    RouteProblem,
    RouteTask,
    RouteUnit,
    plan_routes,
    verify_plan,
)
from routing.run_offline_route_planner import build_result


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
        _hash("test-precondition", identifier),
        _hash("test-effect", identifier),
    )


def _problem(
    tasks,
    units=(RouteUnit("farmer", (4, 4)),),
    shed=(),
    capacity=100,
    budget=24,
    step=0,
    precondition="base",
):
    return RouteProblem(
        step,
        10,
        units,
        shed,
        capacity,
        tuple(tasks),
        budget,
        _hash("test-route-precondition", precondition),
    )


def _plan(problem):
    result = plan_routes(problem)
    assert isinstance(result, RoutePlan)
    assert verify_plan(problem, result) == ()
    return result


def test_single_unit_orders_complete_cluster_route():
    problem = _problem(
        (
            _task("west", (1, 1)),
            _task("middle", (2, 1)),
            _task("east", (3, 1)),
        )
    )
    plan = _plan(problem)
    assert plan.optimal is True
    assert plan.total_actions == 3
    assert plan.total_movement == 6
    assert set(plan.routes[0].task_identifiers) == {"west", "middle", "east"}


def test_exact_assignment_splits_separated_work_between_units():
    problem = _problem(
        (
            _task("nw", (0, 0)),
            _task("se", (9, 9)),
        ),
        (
            RouteUnit("north", (0, 1)),
            RouteUnit("south", (9, 8)),
        ),
    )
    plan = _plan(problem)
    assert tuple(route.task_identifiers for route in plan.routes) == (("nw",), ("se",))
    assert plan.total_cost == 4


def test_dependencies_share_a_unit_and_execute_in_order():
    problem = _problem(
        (
            _task("build", (2, 2), ("BUILD_PASTURE",), priority=1),
            _task(
                "place",
                (2, 2),
                ("PLACE", "COW"),
                priority=0,
                dependencies=("build",),
                requires=(("COW", 1),),
            ),
        ),
        (
            RouteUnit("first", (4, 4)),
            RouteUnit("second", (2, 2)),
        ),
        (("COW", 1),),
    )
    plan = _plan(problem)
    assigned = [route for route in plan.routes if route.task_identifiers]
    assert len(assigned) == 1
    assert assigned[0].task_identifiers == ("build", "place")
    assert assigned[0].pickup == (("COW", 1),)


def test_pickup_consumption_and_harvest_drop_are_verified():
    problem = _problem(
        (
            _task(
                "feed",
                (2, 4),
                ("FEED",),
                priority=0,
                requires=(("WHEAT", 1),),
            ),
            _task(
                "harvest",
                (3, 4),
                ("HARVEST",),
                produces=(("MILK", 2),),
            ),
        ),
        shed=(("WHEAT", 1),),
    )
    plan = _plan(problem)
    route = plan.routes[0]
    assert route.pickup == (("WHEAT", 1),)
    assert route.final_inventory == (("MILK", 2),)
    assert route.commands[-1].action == ("DROP",)


def test_existing_inventory_avoids_pickup_and_returns_remainder():
    problem = _problem(
        (
            _task(
                "feed",
                (3, 4),
                ("FEED",),
                requires=(("WHEAT", 1),),
            ),
        ),
        (RouteUnit("farmer", (3, 4), (("WHEAT", 2),)),),
    )
    plan = _plan(problem)
    assert plan.routes[0].pickup == ()
    assert plan.routes[0].final_inventory == (("WHEAT", 1),)
    assert plan.routes[0].commands[-1].action == ("DROP",)


def test_missing_shared_stock_returns_no_partial_plan():
    problem = _problem(
        (
            _task(
                "feed-a",
                (1, 1),
                ("FEED",),
                requires=(("WHEAT", 1),),
            ),
            _task(
                "feed-b",
                (8, 8),
                ("FEED",),
                requires=(("WHEAT", 1),),
            ),
        ),
        (RouteUnit("first", (4, 4)), RouteUnit("second", (5, 5))),
        (("WHEAT", 1),),
    )
    result = plan_routes(problem)
    assert isinstance(result, RouteFailure)
    assert result.phase == "solve"


def test_shed_overflow_returns_no_partial_plan():
    problem = _problem(
        (_task("harvest", (4, 4), ("HARVEST",), produces=(("MILK", 1),)),),
        shed=(("WHEAT", 100),),
    )
    assert isinstance(plan_routes(problem), RouteFailure)


def test_deadline_and_command_budget_are_hard_constraints():
    urgent = _task("urgent", (0, 0), priority=0, deadline=3)
    problem = _problem((urgent,), units=(RouteUnit("farmer", (4, 4)),), budget=8)
    assert isinstance(plan_routes(problem), RouteFailure)
    tasks = tuple(_task(f"task-{index}", (4, 4), deadline=5) for index in range(6))
    assert isinstance(plan_routes(_problem(tasks, budget=5)), RouteFailure)


def test_more_than_twelve_tasks_uses_verified_fallback():
    tasks = tuple(
        _task(f"task-{index:02d}", (index % 5, index // 5), deadline=24)
        for index in range(13)
    )
    problem = _problem(
        tasks,
        (
            RouteUnit("first", (0, 0)),
            RouteUnit("second", (4, 0)),
            RouteUnit("third", (0, 2)),
        ),
    )
    plan = _plan(problem)
    assert plan.optimal is False
    assert sum(len(route.task_identifiers) for route in plan.routes) == 13


def test_shared_cell_routes_have_explicit_collision_policy():
    problem = _problem(
        (_task("shared-a", (2, 2)), _task("shared-b", (2, 2))),
        (RouteUnit("first", (2, 1)), RouteUnit("second", (2, 3))),
    )
    plan = _plan(problem)
    assert plan.collision_policy == COLLISION_POLICY
    assert COLLISION_POLICY == "shared-cells-allowed"


def test_executor_keeps_plan_during_exact_progress():
    problem = _problem((_task("water", (3, 4)),))
    executor = RouteExecutor()
    plan = executor.prepare(problem)
    assert isinstance(plan, RoutePlan)
    actions = executor.next_actions(((4, 4),))
    assert actions == (("farmer", ("WEST",)),)
    command = plan.routes[0].commands[0]
    accepted = executor.acknowledge(
        (command.identifier,),
        (command.expected_post_position,),
        problem.route_precondition_fingerprint,
    )
    assert accepted is plan
    assert executor.prepare(problem) is plan
    assert executor.cursors == (1,)
    assert executor.next_actions(((3, 4),)) == (("farmer", ("WATER",)),)


def test_executor_clears_route_after_position_or_precondition_change():
    problem = _problem((_task("water", (3, 4)),))
    executor = RouteExecutor()
    plan = executor.prepare(problem)
    assert isinstance(plan, RoutePlan)
    failure = executor.next_actions(((0, 0),))
    assert isinstance(failure, RouteFailure)
    assert executor.plan is None
    plan = executor.prepare(problem)
    executor.next_actions(((4, 4),))
    command = plan.routes[0].commands[0]
    failure = executor.acknowledge(
        (command.identifier,),
        (command.expected_post_position,),
        _hash("test-route-precondition", "changed"),
    )
    assert isinstance(failure, RouteFailure)
    assert executor.plan is None


def test_problem_change_creates_a_new_plan():
    first_problem = _problem((_task("water", (3, 4)),))
    second_problem = _problem(
        (_task("water", (3, 4)),),
        precondition="changed",
    )
    executor = RouteExecutor()
    first = executor.prepare(first_problem)
    second = executor.prepare(second_problem)
    assert isinstance(first, RoutePlan)
    assert isinstance(second, RoutePlan)
    assert second is not first
    assert second.fingerprint != first.fingerprint


def test_verifier_rejects_tampered_totals():
    problem = _problem((_task("water", (3, 4)),))
    plan = _plan(problem)
    tampered = dataclasses.replace(plan, total_cost=plan.total_cost + 1)
    assert "cost total mismatch" in verify_plan(problem, tampered)


def test_input_rejects_cycle_and_last_step_overrun():
    first = _task("first", (1, 1), dependencies=("second",))
    second = _task("second", (1, 2), dependencies=("first",))
    with pytest.raises(ValueError, match="cycle"):
        _problem((first, second))
    with pytest.raises(ValueError, match="remaining"):
        _problem((_task("final", (4, 4), deadline=2),), step=718, budget=2)


def test_registered_runner_is_deterministic_and_verified():
    first = build_result()
    second = build_result()
    assert first == second
    aggregate = first["aggregate"]
    assert aggregate == {
        "registered_scenarios": 8,
        "successful_plans": 7,
        "expected_failures": 1,
        "unexpected_outcomes": [],
        "verification_errors": 0,
        "exact_plans": 6,
        "fallback_plans": 1,
        "route_cost": 89,
        "route_movement": 47,
        "route_actions": 42,
        "legacy_1_14_proxy_cost": 105,
        "proxy_cost_delta": -16,
    }
