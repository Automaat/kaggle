import dataclasses
import importlib
import pathlib
import sys

import pytest


TOOLS = pathlib.Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))
space = importlib.import_module("economics.space_planner")
runner = importlib.import_module("economics.run_space_planner")


def _input(
    cells=(),
    intents=(),
    current_day=0,
    terminal_day=9,
    capacity=10,
    action_value=1.0,
    build_actions=1,
    placement_actions=1,
    service_trips=1,
):
    days = terminal_day - current_day + 1
    capacities = (capacity,) * days if isinstance(capacity, int) else capacity
    return space.SpacePlannerInput(
        current_day,
        terminal_day,
        tuple(cells),
        tuple(intents),
        capacities,
        action_value,
        build_actions,
        placement_actions,
        service_trips,
    )


def _cell(position, kind="EMPTY", unlock=0, crop=None, value=0, release=None):
    return space.SpaceCell(position, unlock, kind, crop, value, release)


def _intent(identifier="animal", animal="GOOSE", day=0, value=100):
    return space.AnimalIntent(identifier, animal, day, value)


def test_no_animal_intent_creates_no_reservation_or_task():
    data = _input(cells=(_cell((4, 4)), _cell((4, 5), "PLANT", crop="CARROT", value=20)))
    result = space.solve_space_plan(data)
    assert result.success
    assert result.variable_count == 0
    assert result.assignments == ()
    assert result.rejected_intents == ()
    assert space.verify_result(data, result) == ()


def test_near_shed_cell_beats_far_cell():
    data = _input(
        cells=(_cell((0, 0)), _cell((4, 4))),
        intents=(_intent(),),
        service_trips=2,
    )
    result = space.solve_space_plan(data)
    assert result.assignments[0].position == (4, 4)
    assert result.assignments[0].distance == 0


def test_low_value_crop_is_removed_for_immediate_animal():
    data = _input(
        cells=(_cell((4, 4), "PLANT", crop="CARROT", value=5, release=5),),
        intents=(_intent(),),
        service_trips=0,
    )
    result = space.solve_space_plan(data)
    assignment = result.assignments[0]
    assert assignment.mode == "dig_crop"
    assert assignment.destroyed_crop_value == 5
    assert [task.operation for task in assignment.tasks] == [
        "DIG",
        "BUILD_COOP",
        "PLACE",
    ]


def test_high_value_crop_is_kept_until_natural_release():
    data = _input(
        cells=(_cell((4, 4), "PLANT", crop="STRAWBERRY", value=1000, release=5),),
        intents=(_intent(),),
        service_trips=0,
    )
    result = space.solve_space_plan(data)
    assignment = result.assignments[0]
    assert assignment.mode == "wait_crop"
    assert assignment.placement_day == 5
    assert assignment.destroyed_crop_value == 0
    assert [task.operation for task in assignment.tasks] == ["BUILD_COOP", "PLACE"]


def test_matching_structure_avoids_build_work():
    data = _input(
        cells=(_cell((4, 4), "COOP"), _cell((4, 5))),
        intents=(_intent(),),
        service_trips=0,
        build_actions=3,
    )
    result = space.solve_space_plan(data)
    assignment = result.assignments[0]
    assert assignment.position == (4, 4)
    assert assignment.mode == "use_structure"
    assert assignment.transition_actions == 1
    assert [task.operation for task in assignment.tasks] == ["PLACE"]


def test_multiple_intents_use_unique_cells_and_daily_capacity():
    data = _input(
        cells=(_cell((4, 4)), _cell((5, 4))),
        intents=(
            _intent("first", day=0, value=110),
            _intent("second", animal="SHEEP", day=0, value=100),
        ),
        terminal_day=3,
        capacity=(2, 2, 0, 0),
        service_trips=0,
    )
    result = space.solve_space_plan(data)
    assert len(result.assignments) == 2
    assert len({assignment.position for assignment in result.assignments}) == 2
    assert [assignment.placement_day for assignment in result.assignments] == [0, 1]
    assert space.verify_result(data, result) == ()


def test_future_central_land_beats_old_remote_land():
    data = _input(
        cells=(_cell((0, 0)), _cell((5, 4), unlock=10)),
        intents=(_intent(day=10),),
        terminal_day=20,
        service_trips=2,
    )
    result = space.solve_space_plan(data)
    assignment = result.assignments[0]
    assert assignment.position == (5, 4)
    assert assignment.mode == "future_land"
    assert assignment.placement_day == 10


def test_negative_late_animal_is_skipped():
    data = _input(
        cells=(_cell((4, 4)),),
        intents=(_intent(day=29, value=1),),
        current_day=29,
        terminal_day=29,
        service_trips=0,
    )
    result = space.solve_space_plan(data)
    assert result.assignments == ()
    assert result.rejected_intents == ("animal",)
    assert result.objective_value == 0


def test_weed_requires_dig_before_build_and_place():
    data = _input(
        cells=(_cell((4, 4), "WEED"),),
        intents=(_intent(),),
        service_trips=0,
    )
    assignment = space.solve_space_plan(data).assignments[0]
    assert assignment.mode == "clear_weed"
    assert assignment.transition_actions == 3
    assert [task.operation for task in assignment.tasks] == [
        "DIG",
        "BUILD_COOP",
        "PLACE",
    ]


def test_crop_without_horizon_release_can_only_be_removed():
    data = _input(
        cells=(_cell((4, 4), "PLANT", crop="STRAWBERRY", value=5),),
        intents=(_intent(),),
        service_trips=0,
    )
    candidates = space.generate_candidates(data)
    assert [candidate.mode for candidate in candidates] == ["dig_crop"]


def test_incompatible_structure_is_not_destroyed():
    data = _input(
        cells=(_cell((4, 4), "PASTURE"),),
        intents=(_intent(animal="GOOSE"),),
    )
    result = space.solve_space_plan(data)
    assert result.variable_count == 0
    assert result.assignments == ()


def test_transition_work_uses_capacity_without_repeated_tasks():
    data = _input(
        cells=(_cell((4, 4)),),
        intents=(_intent(),),
        capacity=4,
        build_actions=3,
        placement_actions=1,
        service_trips=0,
    )
    assignment = space.solve_space_plan(data).assignments[0]
    assert assignment.transition_actions == 4
    assert [task.operation for task in assignment.tasks] == ["BUILD_COOP", "PLACE"]
    assert [task.action_count for task in assignment.tasks] == [3, 1]


def test_tasks_never_move_a_plant_or_animal():
    data = _input(
        cells=(_cell((4, 4), "PLANT", crop="CARROT", value=5, release=5),),
        intents=(_intent(),),
        service_trips=0,
    )
    result = space.solve_space_plan(data)
    assert all(task.operation != "MOVE" for task in result.assignments[0].tasks)


def test_input_hash_is_deterministic_and_sensitive():
    data = _input(cells=(_cell((4, 4)),), intents=(_intent(),))
    changed = dataclasses.replace(data, action_value=2)
    assert space.input_sha256(data) == space.input_sha256(data)
    assert space.input_sha256(data) != space.input_sha256(changed)


def test_verifier_rejects_modified_assignment_and_objective():
    data = _input(cells=(_cell((4, 4)),), intents=(_intent(),))
    result = space.solve_space_plan(data)
    changed_assignment = dataclasses.replace(result.assignments[0], distance=1)
    changed = dataclasses.replace(
        result,
        assignments=(changed_assignment,),
        objective_value=result.objective_value + 1,
    )
    assert space.verify_result(data, changed) == (
        "assignment mismatch",
        "rejected intents mismatch",
        "objective mismatch",
    )


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("build_actions", 0, ValueError),
        ("action_capacity", (True,) * 10, ValueError),
        ("current_day", True, TypeError),
    ],
)
def test_input_rejects_invalid_numeric_settings(field, value, error):
    values = dataclasses.asdict(_input())
    values[field] = value
    with pytest.raises(error):
        space.SpacePlannerInput(**values)


def test_registered_cases_pass_solver_and_verifier_gates():
    result = runner.run()
    assert result["case_count"] == 8
    assert result["successful_cases"] == 8
    assert result["maximum_mip_gap"] == 0
    assert result["verification_error_count"] == 0
    assert result["realized_simulator_score"] is None
    assert result["live_agent_changed"] is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("time_limit", float("nan")),
        ("time_limit", float("inf")),
        ("mip_rel_gap", float("nan")),
        ("mip_rel_gap", float("inf")),
    ],
)
def test_solver_rejects_nonfinite_settings(field, value):
    data = _input(cells=(_cell((4, 4)),), intents=(_intent(),))
    values = {field: value}
    with pytest.raises(ValueError):
        space.solve_space_plan(data, **values)
