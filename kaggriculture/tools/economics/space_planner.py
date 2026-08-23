import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_array


LAST_DAY = 29
ANIMAL_STRUCTURES = {
    "GOOSE": "COOP",
    "COW": "PASTURE",
    "SHEEP": "PASTURE",
}
CELL_KINDS = frozenset({"EMPTY", "WEED", "PLANT", "COOP", "PASTURE", "ANIMAL"})


@dataclass(frozen=True, slots=True)
class SpaceCell:
    position: tuple[int, int]
    unlock_day: int
    kind: str
    crop: str | None = None
    remaining_crop_value: float = 0.0
    release_day: int | None = None

    def __post_init__(self):
        if (
            type(self.position) is not tuple
            or len(self.position) != 2
            or any(type(value) is not int for value in self.position)
        ):
            raise TypeError("cell position must be an integer pair")
        if any(value < 0 or value >= 10 for value in self.position):
            raise ValueError("cell position must be on board")
        if type(self.unlock_day) is not int or not 0 <= self.unlock_day <= LAST_DAY:
            raise ValueError("cell unlock day must be in season")
        if self.kind not in CELL_KINDS:
            raise ValueError("unknown cell kind")
        if type(self.remaining_crop_value) not in (int, float) or isinstance(
            self.remaining_crop_value,
            bool,
        ):
            raise TypeError("crop value must be numeric")
        if not math.isfinite(self.remaining_crop_value) or self.remaining_crop_value < 0:
            raise ValueError("crop value must be finite and nonnegative")
        if self.kind == "PLANT":
            if type(self.crop) is not str or not self.crop:
                raise ValueError("plant cell must identify its crop")
            if self.release_day is not None and type(self.release_day) is not int:
                raise TypeError("plant release day must be an integer or None")
            if self.release_day is not None and not (
                self.unlock_day <= self.release_day <= LAST_DAY
            ):
                raise ValueError("plant release must follow unlock inside season")
        elif self.crop is not None or self.remaining_crop_value != 0 or self.release_day is not None:
            raise ValueError("nonplant cell cannot contain crop facts")


@dataclass(frozen=True, slots=True)
class AnimalIntent:
    identifier: str
    animal: str
    purchase_day: int
    daily_value: float

    def __post_init__(self):
        if type(self.identifier) is not str or not self.identifier:
            raise ValueError("intent identifier must be nonempty")
        if self.animal not in ANIMAL_STRUCTURES:
            raise ValueError("unknown animal")
        if type(self.purchase_day) is not int or not 0 <= self.purchase_day <= LAST_DAY:
            raise ValueError("purchase day must be in season")
        if type(self.daily_value) not in (int, float) or isinstance(
            self.daily_value,
            bool,
        ):
            raise TypeError("daily value must be numeric")
        if not math.isfinite(self.daily_value) or self.daily_value < 0:
            raise ValueError("daily value must be finite and nonnegative")


@dataclass(frozen=True, slots=True)
class SpacePlannerInput:
    current_day: int
    terminal_day: int
    cells: tuple[SpaceCell, ...]
    intents: tuple[AnimalIntent, ...]
    action_capacity: tuple[int, ...]
    action_value: float
    build_actions: int
    placement_actions: int
    daily_service_trips: int

    def __post_init__(self):
        integers = (
            self.current_day,
            self.terminal_day,
            self.build_actions,
            self.placement_actions,
            self.daily_service_trips,
        )
        if any(type(value) is not int for value in integers):
            raise TypeError("planner integer settings must be integers")
        if not 0 <= self.current_day <= self.terminal_day <= LAST_DAY:
            raise ValueError("planner horizon must be inside season")
        if self.build_actions < 1 or self.placement_actions < 1:
            raise ValueError("transition actions must be valid")
        if self.daily_service_trips < 0:
            raise ValueError("service trips must be nonnegative")
        if type(self.action_value) not in (int, float) or isinstance(
            self.action_value,
            bool,
        ):
            raise TypeError("action value must be numeric")
        if not math.isfinite(self.action_value) or self.action_value < 0:
            raise ValueError("action value must be finite and nonnegative")
        if type(self.cells) is not tuple or any(
            not isinstance(cell, SpaceCell) for cell in self.cells
        ):
            raise TypeError("cells must be SpaceCell values")
        if len({cell.position for cell in self.cells}) != len(self.cells):
            raise ValueError("cell positions must be unique")
        if type(self.intents) is not tuple or any(
            not isinstance(intent, AnimalIntent) for intent in self.intents
        ):
            raise TypeError("intents must be AnimalIntent values")
        if len({intent.identifier for intent in self.intents}) != len(self.intents):
            raise ValueError("intent identifiers must be unique")
        horizon = self.terminal_day - self.current_day + 1
        if type(self.action_capacity) is not tuple or len(self.action_capacity) != horizon:
            raise TypeError("action capacity must cover horizon")
        if any(type(value) is not int or value < 0 for value in self.action_capacity):
            raise ValueError("action capacity must be nonnegative integers")


@dataclass(frozen=True, slots=True)
class SpaceTask:
    day: int
    position: tuple[int, int]
    operation: str
    subject: str
    action_count: int


@dataclass(frozen=True, slots=True)
class SpaceAssignment:
    intent: str
    animal: str
    position: tuple[int, int]
    mode: str
    placement_day: int
    distance: int
    destroyed_crop_value: float
    net_value: float
    transition_actions: int
    tasks: tuple[SpaceTask, ...]


@dataclass(frozen=True, slots=True)
class SpacePlannerResult:
    success: bool
    status: int
    message: str
    mip_gap: float | None
    wall_seconds: float
    variable_count: int
    constraint_count: int
    objective_value: float | None
    assignments: tuple[SpaceAssignment, ...]
    rejected_intents: tuple[str, ...]
    input_sha256: str


@dataclass(frozen=True, slots=True)
class _Candidate:
    identifier: str
    intent: str
    animal: str
    position: tuple[int, int]
    mode: str
    placement_day: int
    distance: int
    destroyed_crop_value: float
    net_value: float
    transition_actions: int
    tasks: tuple[SpaceTask, ...]


def input_sha256(data):
    if not isinstance(data, SpacePlannerInput):
        raise TypeError("data must be SpacePlannerInput")
    encoded = json.dumps(asdict(data), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _shed_distance(position):
    x, y = position
    return min(
        abs(x - shed_x) + abs(y - shed_y)
        for shed_x, shed_y in ((4, 4), (5, 4), (4, 5), (5, 5))
    )


def _tasks(cell, intent, day, mode, data):
    result = []
    structure = ANIMAL_STRUCTURES[intent.animal]
    if mode in ("dig_crop", "clear_weed"):
        result.append(
            SpaceTask(day, cell.position, "DIG", cell.crop or cell.kind, 1)
        )
    compatible = cell.kind == structure and mode == "use_structure"
    if not compatible:
        result.append(
            SpaceTask(
                day,
                cell.position,
                f"BUILD_{structure}",
                intent.animal,
                data.build_actions,
            )
        )
    result.append(
        SpaceTask(
            day,
            cell.position,
            "PLACE",
            intent.animal,
            data.placement_actions,
        )
    )
    return tuple(result)


def _transition_actions(tasks):
    return sum(task.action_count for task in tasks)


def _candidate(data, cell, intent, mode, placement_day, crop_loss):
    if placement_day > data.terminal_day:
        return None
    tasks = _tasks(cell, intent, placement_day, mode, data)
    distance = _shed_distance(cell.position)
    active_days = data.terminal_day - placement_day + 1
    gross = active_days * intent.daily_value
    travel = (
        distance
        * data.daily_service_trips
        * active_days
        * data.action_value
    )
    transition_actions = _transition_actions(tasks)
    transition = transition_actions * data.action_value
    net = gross - travel - transition - crop_loss
    return _Candidate(
        f"{intent.identifier}:{cell.position[0]}:{cell.position[1]}:{mode}",
        intent.identifier,
        intent.animal,
        cell.position,
        mode,
        placement_day,
        distance,
        float(crop_loss),
        float(net),
        transition_actions,
        tasks,
    )


def generate_candidates(data):
    if not isinstance(data, SpacePlannerInput):
        raise TypeError("data must be SpacePlannerInput")
    result = []
    for intent in data.intents:
        for cell in data.cells:
            if cell.kind == "ANIMAL":
                continue
            first_day = max(data.current_day, intent.purchase_day, cell.unlock_day)
            structure = ANIMAL_STRUCTURES[intent.animal]
            if cell.kind == "EMPTY":
                mode = "future_land" if cell.unlock_day > data.current_day else "use_empty"
                for day in range(first_day, data.terminal_day + 1):
                    candidate = _candidate(data, cell, intent, mode, day, 0)
                    if candidate is not None:
                        result.append(candidate)
            elif cell.kind in ("COOP", "PASTURE"):
                if cell.kind == structure:
                    for day in range(first_day, data.terminal_day + 1):
                        candidate = _candidate(
                            data,
                            cell,
                            intent,
                            "use_structure",
                            day,
                            0,
                        )
                        if candidate is not None:
                            result.append(candidate)
            elif cell.kind == "WEED":
                for day in range(first_day, data.terminal_day + 1):
                    candidate = _candidate(data, cell, intent, "clear_weed", day, 0)
                    if candidate is not None:
                        result.append(candidate)
            elif cell.kind == "PLANT":
                if cell.release_day is not None:
                    wait_day = max(first_day, cell.release_day)
                    for day in range(wait_day, data.terminal_day + 1):
                        waited = _candidate(data, cell, intent, "wait_crop", day, 0)
                        if waited is not None:
                            result.append(waited)
                removed = _candidate(
                    data,
                    cell,
                    intent,
                    "dig_crop",
                    first_day,
                    cell.remaining_crop_value,
                )
                if removed is not None:
                    result.append(removed)
    return tuple(result)


def _arrays(data, candidates):
    count = len(candidates)
    objective = np.asarray([-candidate.net_value for candidate in candidates])
    integrality = np.ones(count)
    bounds = Bounds(
        np.zeros(count),
        np.asarray([1 if candidate.net_value > 0 else 0 for candidate in candidates]),
    )
    rows = []
    lower = []
    upper = []
    for intent in data.intents:
        rows.append(
            {index: 1 for index, candidate in enumerate(candidates) if candidate.intent == intent.identifier}
        )
        lower.append(0)
        upper.append(1)
    for cell in data.cells:
        rows.append(
            {index: 1 for index, candidate in enumerate(candidates) if candidate.position == cell.position}
        )
        lower.append(0)
        upper.append(1)
    for day in range(data.current_day, data.terminal_day + 1):
        rows.append(
            {
                index: candidate.transition_actions
                for index, candidate in enumerate(candidates)
                if candidate.placement_day == day
            }
        )
        lower.append(0)
        upper.append(data.action_capacity[day - data.current_day])
    row_indices = []
    columns = []
    values = []
    for row, coefficients in enumerate(rows):
        for column, value in coefficients.items():
            if value:
                row_indices.append(row)
                columns.append(column)
                values.append(value)
    matrix = coo_array(
        (values, (row_indices, columns)),
        shape=(len(rows), count),
    ).tocsr()
    constraints = LinearConstraint(matrix, np.asarray(lower), np.asarray(upper))
    return objective, integrality, bounds, constraints, len(rows)


def solve_space_plan(data, time_limit=30.0, mip_rel_gap=0.0):
    if not isinstance(data, SpacePlannerInput):
        raise TypeError("data must be SpacePlannerInput")
    if (
        type(time_limit) not in (int, float)
        or not math.isfinite(time_limit)
        or time_limit <= 0
    ):
        raise ValueError("time limit must be positive")
    if (
        type(mip_rel_gap) not in (int, float)
        or not math.isfinite(mip_rel_gap)
        or not 0 <= mip_rel_gap < 1
    ):
        raise ValueError("MIP gap must be in 0..1")
    candidates = generate_candidates(data)
    if not candidates:
        return SpacePlannerResult(
            True,
            0,
            "no candidates",
            0.0,
            0.0,
            0,
            len(data.intents) + len(data.cells) + len(data.action_capacity),
            0.0,
            (),
            tuple(intent.identifier for intent in data.intents),
            input_sha256(data),
        )
    objective, integrality, bounds, constraints, row_count = _arrays(data, candidates)
    started = time.perf_counter()
    solved = milp(
        objective,
        integrality=integrality,
        bounds=bounds,
        constraints=constraints,
        options={"time_limit": float(time_limit), "mip_rel_gap": float(mip_rel_gap)},
    )
    wall_seconds = time.perf_counter() - started
    success = bool(solved.success and solved.x is not None)
    gap = getattr(solved, "mip_gap", None)
    gap = float(gap) if gap is not None and math.isfinite(float(gap)) else None
    if not success:
        return SpacePlannerResult(
            False,
            int(solved.status),
            str(solved.message),
            gap,
            wall_seconds,
            len(candidates),
            row_count,
            None,
            (),
            (),
            input_sha256(data),
        )
    selected = tuple(
        candidate
        for index, candidate in enumerate(candidates)
        if int(round(float(solved.x[index]))) == 1
    )
    assignments = tuple(
        SpaceAssignment(
            candidate.intent,
            candidate.animal,
            candidate.position,
            candidate.mode,
            candidate.placement_day,
            candidate.distance,
            candidate.destroyed_crop_value,
            candidate.net_value,
            candidate.transition_actions,
            candidate.tasks,
        )
        for candidate in sorted(selected, key=lambda value: value.intent)
    )
    assigned = {assignment.intent for assignment in assignments}
    rejected = tuple(
        intent.identifier for intent in data.intents if intent.identifier not in assigned
    )
    return SpacePlannerResult(
        True,
        int(solved.status),
        str(solved.message),
        gap,
        wall_seconds,
        len(candidates),
        row_count,
        sum(assignment.net_value for assignment in assignments),
        assignments,
        rejected,
        input_sha256(data),
    )


def verify_result(data, result):
    if not isinstance(data, SpacePlannerInput):
        raise TypeError("data must be SpacePlannerInput")
    if not isinstance(result, SpacePlannerResult):
        raise TypeError("result must be SpacePlannerResult")
    errors = []
    if result.input_sha256 != input_sha256(data):
        errors.append("input hash mismatch")
    if not result.success:
        return tuple(errors)
    candidates = {
        (
            candidate.intent,
            candidate.position,
            candidate.mode,
            candidate.placement_day,
        ): candidate
        for candidate in generate_candidates(data)
    }
    selected = []
    for assignment in result.assignments:
        key = (
            assignment.intent,
            assignment.position,
            assignment.mode,
            assignment.placement_day,
        )
        candidate = candidates.get(key)
        if candidate is None:
            errors.append("unknown assignment")
            continue
        if assignment != SpaceAssignment(
            candidate.intent,
            candidate.animal,
            candidate.position,
            candidate.mode,
            candidate.placement_day,
            candidate.distance,
            candidate.destroyed_crop_value,
            candidate.net_value,
            candidate.transition_actions,
            candidate.tasks,
        ):
            errors.append("assignment mismatch")
            continue
        selected.append(candidate)
    if len({candidate.intent for candidate in selected}) != len(selected):
        errors.append("intent assigned more than once")
    if len({candidate.position for candidate in selected}) != len(selected):
        errors.append("cell assigned more than once")
    for day in range(data.current_day, data.terminal_day + 1):
        used = sum(
            candidate.transition_actions
            for candidate in selected
            if candidate.placement_day == day
        )
        if used > data.action_capacity[day - data.current_day]:
            errors.append("action capacity exceeded")
    expected_rejected = tuple(
        intent.identifier
        for intent in data.intents
        if intent.identifier not in {candidate.intent for candidate in selected}
    )
    if result.rejected_intents != expected_rejected:
        errors.append("rejected intents mismatch")
    objective = sum(candidate.net_value for candidate in selected)
    if result.objective_value is None or not math.isclose(
        result.objective_value,
        objective,
        abs_tol=1e-7,
    ):
        errors.append("objective mismatch")
    if any(assignment.net_value < -1e-7 for assignment in result.assignments):
        errors.append("negative assignment selected")
    return tuple(errors)
