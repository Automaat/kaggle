from dataclasses import dataclass

from economics.market_ledger import ANIMALS, CROPS, PRODUCTS, SHED_ITEMS
from economics.rolling_coordinator import canonical_sha256


MOVES = {
    "NORTH": (0, -1),
    "SOUTH": (0, 1),
    "EAST": (1, 0),
    "WEST": (-1, 0),
}
TASK_OPERATIONS = frozenset(
    {
        "BUILD_COOP",
        "BUILD_PASTURE",
        "CARE",
        "COLLECT_FERTILIZER",
        "DIG",
        "FEED",
        "FERTILIZE",
        "HARVEST",
        "PLACE",
        "PLANT",
        "WATER",
    }
)
EXACT_TASK_LIMIT = 12
COLLISION_POLICY = "shared-cells-allowed"


def _validate_fingerprint(value, name):
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be canonical SHA-256")


def _validate_identifier(value, name):
    if type(value) is not str or not value:
        raise ValueError(f"{name} must be nonempty text")


def _validate_position(position, board_size, name):
    if type(position) is not tuple or len(position) != 2:
        raise TypeError(f"{name} must be a pair")
    if any(type(value) is not int for value in position):
        raise TypeError(f"{name} coordinates must be integers")
    if any(value < 0 or value >= board_size for value in position):
        raise ValueError(f"{name} must be on the board")


def _validate_inventory(entries, name):
    if type(entries) is not tuple:
        raise TypeError(f"{name} must be a tuple")
    seen = set()
    for entry in entries:
        if type(entry) is not tuple or len(entry) != 2:
            raise TypeError(f"{name} entries must be pairs")
        item, quantity = entry
        if item not in SHED_ITEMS:
            raise ValueError(f"{name} contains an unknown item")
        if type(quantity) is not int:
            raise TypeError(f"{name} quantities must be integers")
        if quantity <= 0:
            raise ValueError(f"{name} quantities must be positive")
        if item in seen:
            raise ValueError(f"{name} items must be unique")
        seen.add(item)


def _inventory_vector(entries):
    values = [0] * len(SHED_ITEMS)
    for item, quantity in entries:
        values[SHED_ITEMS.index(item)] = quantity
    return tuple(values)


def _vector_add(first, second):
    return tuple(left + right for left, right in zip(first, second, strict=True))


def _vector_subtract(first, second):
    return tuple(left - right for left, right in zip(first, second, strict=True))


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


def _path(start, end):
    position = start
    commands = []
    while position[0] < end[0]:
        commands.append("EAST")
        position = (position[0] + 1, position[1])
    while position[0] > end[0]:
        commands.append("WEST")
        position = (position[0] - 1, position[1])
    while position[1] < end[1]:
        commands.append("SOUTH")
        position = (position[0], position[1] + 1)
    while position[1] > end[1]:
        commands.append("NORTH")
        position = (position[0], position[1] - 1)
    return tuple(commands)


@dataclass(frozen=True, slots=True)
class RouteTask:
    identifier: str
    position: tuple[int, int]
    action: tuple
    priority: int
    deadline_turn: int
    dependencies: tuple[str, ...] = ()
    requires: tuple[tuple[str, int], ...] = ()
    produces: tuple[tuple[str, int], ...] = ()
    precondition_fingerprint: str = ""
    effect_fingerprint: str = ""

    def __post_init__(self):
        _validate_identifier(self.identifier, "task identifier")
        if type(self.action) is not tuple or not self.action:
            raise TypeError("task action must be a nonempty tuple")
        if type(self.action[0]) is not str or self.action[0] not in TASK_OPERATIONS:
            raise ValueError("task action is unsupported")
        if self.action[0] in ("PLACE", "PLANT"):
            if len(self.action) != 2 or type(self.action[1]) is not str:
                raise ValueError("item task action must contain one item")
            allowed = ANIMALS if self.action[0] == "PLACE" else CROPS
            if self.action[1] not in allowed:
                raise ValueError("task action contains an unknown item")
        elif len(self.action) != 1:
            raise ValueError("task action has extra values")
        if type(self.priority) is not int or self.priority < 0:
            raise ValueError("task priority must be a nonnegative integer")
        if type(self.deadline_turn) is not int or self.deadline_turn < 1:
            raise ValueError("task deadline must be positive")
        if type(self.dependencies) is not tuple:
            raise TypeError("task dependencies must be a tuple")
        for dependency in self.dependencies:
            _validate_identifier(dependency, "task dependency")
        if self.identifier in self.dependencies or len(set(self.dependencies)) != len(
            self.dependencies
        ):
            raise ValueError("task dependencies are invalid")
        _validate_inventory(self.requires, "task requirements")
        _validate_inventory(self.produces, "task production")
        expected_requirements = {
            "FEED": (("WHEAT", 1),),
            "FERTILIZE": (("FERTILIZER", 1),),
            "PLACE": ((self.action[1], 1),) if self.action[0] == "PLACE" else (),
        }.get(self.action[0], ())
        if self.requires != expected_requirements:
            raise ValueError("task requirements do not match its action")
        if self.action[0] == "COLLECT_FERTILIZER":
            if self.produces != (("FERTILIZER", 1),):
                raise ValueError("task production does not match its action")
        elif self.action[0] == "HARVEST":
            harvest_items = frozenset(PRODUCTS) - {"FERTILIZER"}
            if len(self.produces) != 1 or any(
                item not in harvest_items for item, _quantity in self.produces
            ):
                raise ValueError("task production does not match its action")
        elif self.produces:
            raise ValueError("task production does not match its action")
        _validate_fingerprint(self.precondition_fingerprint, "task precondition")
        _validate_fingerprint(self.effect_fingerprint, "task effect")


@dataclass(frozen=True, slots=True)
class RouteUnit:
    identifier: str
    position: tuple[int, int]
    inventory: tuple[tuple[str, int], ...] = ()

    def __post_init__(self):
        _validate_identifier(self.identifier, "unit identifier")
        _validate_inventory(self.inventory, "unit inventory")


@dataclass(frozen=True, slots=True)
class RouteProblem:
    source_step: int
    board_size: int
    units: tuple[RouteUnit, ...]
    shed: tuple[tuple[str, int], ...]
    shed_capacity: int
    tasks: tuple[RouteTask, ...]
    max_commands_per_unit: int
    route_precondition_fingerprint: str

    def __post_init__(self):
        if type(self.source_step) is not int or not 0 <= self.source_step <= 718:
            raise ValueError("source step must be in 0..718")
        if type(self.board_size) is not int or self.board_size < 2:
            raise ValueError("board size must be at least two")
        if type(self.units) is not tuple or not self.units:
            raise TypeError("units must be a nonempty tuple")
        if any(type(unit) is not RouteUnit for unit in self.units):
            raise TypeError("units have wrong type")
        unit_ids = tuple(unit.identifier for unit in self.units)
        if len(set(unit_ids)) != len(unit_ids):
            raise ValueError("unit identifiers must be unique")
        for unit in self.units:
            _validate_position(unit.position, self.board_size, "unit position")
        _validate_inventory(self.shed, "shed inventory")
        if type(self.shed_capacity) is not int or self.shed_capacity < 1:
            raise ValueError("shed capacity must be positive")
        if sum(quantity for _item, quantity in self.shed) > self.shed_capacity:
            raise ValueError("shed inventory exceeds capacity")
        if type(self.tasks) is not tuple:
            raise TypeError("tasks must be a tuple")
        if any(type(task) is not RouteTask for task in self.tasks):
            raise TypeError("tasks have wrong type")
        task_ids = tuple(task.identifier for task in self.tasks)
        if len(set(task_ids)) != len(task_ids):
            raise ValueError("task identifiers must be unique")
        if type(self.max_commands_per_unit) is not int:
            raise TypeError("command budget must be an integer")
        known = set(task_ids)
        for task in self.tasks:
            _validate_position(task.position, self.board_size, "task position")
            if any(dependency not in known for dependency in task.dependencies):
                raise ValueError("task dependency is missing")
            if task.deadline_turn > self.max_commands_per_unit:
                raise ValueError("task deadline exceeds command budget")
        _validate_acyclic(self.tasks)
        remaining = 24 - self.source_step % 24
        if self.source_step // 24 == 29:
            remaining = 719 - self.source_step
        if not 1 <= self.max_commands_per_unit <= remaining:
            raise ValueError("command budget exceeds remaining processed turns")
        _validate_fingerprint(
            self.route_precondition_fingerprint,
            "route precondition",
        )

    @property
    def fingerprint(self):
        return canonical_sha256(
            "offline-route-problem",
            (
                self.source_step,
                self.board_size,
                tuple(
                    (unit.identifier, unit.position, unit.inventory)
                    for unit in self.units
                ),
                self.shed,
                self.shed_capacity,
                tuple(
                    (
                        task.identifier,
                        task.position,
                        task.action,
                        task.priority,
                        task.deadline_turn,
                        task.dependencies,
                        task.requires,
                        task.produces,
                        task.precondition_fingerprint,
                        task.effect_fingerprint,
                    )
                    for task in self.tasks
                ),
                self.max_commands_per_unit,
                self.route_precondition_fingerprint,
            ),
        )


def _validate_acyclic(tasks):
    by_id = {task.identifier: task for task in tasks}
    active = set()
    complete = set()

    def visit(identifier):
        if identifier in active:
            raise ValueError("task dependencies contain a cycle")
        if identifier in complete:
            return
        active.add(identifier)
        for dependency in by_id[identifier].dependencies:
            visit(dependency)
        active.remove(identifier)
        complete.add(identifier)

    for task in tasks:
        visit(task.identifier)


@dataclass(frozen=True, slots=True)
class PlannedCommand:
    identifier: str
    expected_pre_position: tuple[int, int]
    action: tuple
    expected_post_position: tuple[int, int]
    task_identifier: str
    effect_fingerprint: str


@dataclass(frozen=True, slots=True)
class UnitRoute:
    unit_identifier: str
    task_identifiers: tuple[str, ...]
    commands: tuple[PlannedCommand, ...]
    movement_count: int
    action_count: int
    pickup: tuple[tuple[str, int], ...]
    final_inventory: tuple[tuple[str, int], ...]

    @property
    def command_count(self):
        return len(self.commands)


@dataclass(frozen=True, slots=True)
class RoutePlan:
    problem_fingerprint: str
    route_precondition_fingerprint: str
    routes: tuple[UnitRoute, ...]
    total_cost: int
    total_movement: int
    total_actions: int
    maximum_unit_load: int
    optimal: bool
    collision_policy: str
    fingerprint: str


@dataclass(frozen=True, slots=True)
class RouteFailure:
    phase: str
    message: str
    problem_fingerprint: str | None


@dataclass(frozen=True, slots=True)
class _RouteCandidate:
    mask: int
    route: UnitRoute
    pickup_vector: tuple[int, ...]
    final_vector: tuple[int, ...]


def _component_masks(tasks):
    index = {task.identifier: position for position, task in enumerate(tasks)}
    links = [set() for _task in tasks]
    for position, task in enumerate(tasks):
        for dependency in task.dependencies:
            other = index[dependency]
            links[position].add(other)
            links[other].add(position)
    remaining = set(range(len(tasks)))
    masks = []
    while remaining:
        start = min(remaining)
        stack = [start]
        mask = 0
        while stack:
            current = stack.pop()
            if current not in remaining:
                continue
            remaining.remove(current)
            mask |= 1 << current
            stack.extend(sorted(links[current], reverse=True))
        masks.append(mask)
    return tuple(masks)


def _is_component_closed(mask, component_masks):
    return all(mask & component in (0, component) for component in component_masks)


def _task_vectors(tasks, mask):
    required = [0] * len(SHED_ITEMS)
    produced = [0] * len(SHED_ITEMS)
    for index, task in enumerate(tasks):
        if not mask & (1 << index):
            continue
        for item, quantity in task.requires:
            required[SHED_ITEMS.index(item)] += quantity
        for item, quantity in task.produces:
            produced[SHED_ITEMS.index(item)] += quantity
    return tuple(required), tuple(produced)


def _command(position, action, post_position, task_identifier, effect):
    identifier = canonical_sha256(
        "offline-route-command",
        (position, action, post_position, task_identifier, effect),
    )
    return PlannedCommand(
        identifier,
        position,
        action,
        post_position,
        task_identifier,
        effect,
    )


def _append_path(commands, position, destination, target_identifier):
    current = position
    for operation in _path(position, destination):
        dx, dy = MOVES[operation]
        after = (current[0] + dx, current[1] + dy)
        effect = canonical_sha256(
            "offline-route-move-effect",
            (current, after, target_identifier),
        )
        commands.append(
            _command(current, (operation,), after, target_identifier, effect)
        )
        current = after
    return current


def _best_task_orders(tasks, selected, start, initial_elapsed):
    local = tuple(index for index in range(len(tasks)) if selected & (1 << index))
    if not local:
        return (((), initial_elapsed),)
    local_bit = {task_index: 1 << position for position, task_index in enumerate(local)}
    index_by_id = {task.identifier: index for index, task in enumerate(tasks)}
    dependencies = {
        task_index: sum(
            local_bit[index_by_id[dependency]]
            for dependency in tasks[task_index].dependencies
        )
        for task_index in local
    }
    states = {(0, -1): (initial_elapsed, (), ())}
    full = (1 << len(local)) - 1
    for _count in range(len(local)):
        updated = dict(states)
        for (visited, last), (elapsed, priority_key, order) in states.items():
            position = start if last < 0 else tasks[local[last]].position
            for local_position, task_index in enumerate(local):
                bit = 1 << local_position
                if visited & bit or dependencies[task_index] & ~visited:
                    continue
                task = tasks[task_index]
                completion = elapsed + _distance(position, task.position) + 1
                if completion > task.deadline_turn:
                    continue
                new_visited = visited | bit
                new_order = (*order, task_index)
                new_priority = (*priority_key, (task.priority, task.identifier))
                key = (new_visited, local_position)
                value = (completion, new_priority, new_order)
                previous = updated.get(key)
                if previous is None or value < previous:
                    updated[key] = value
        states = updated
    complete = [value for (visited, _last), value in states.items() if visited == full]
    if not complete:
        return None
    return tuple(
        (order, elapsed)
        for elapsed, _priority, order in sorted(complete)
    )


def _heuristic_task_order(tasks, selected, start, initial_elapsed):
    index_by_id = {task.identifier: index for index, task in enumerate(tasks)}
    remaining = {
        index for index in range(len(tasks)) if selected & (1 << index)
    }
    visited = set()
    order = []
    position = start
    elapsed = initial_elapsed
    while remaining:
        choices = []
        for task_index in remaining:
            task = tasks[task_index]
            if any(index_by_id[dependency] not in visited for dependency in task.dependencies):
                continue
            completion = elapsed + _distance(position, task.position) + 1
            if completion > task.deadline_turn:
                continue
            choices.append(
                (
                    task.deadline_turn,
                    task.priority,
                    _distance(position, task.position),
                    task.identifier,
                    task_index,
                    completion,
                )
            )
        if not choices:
            return None
        _deadline, _priority, _travel, _identifier, task_index, elapsed = min(choices)
        remaining.remove(task_index)
        visited.add(task_index)
        order.append(task_index)
        position = tasks[task_index].position
    return ((tuple(order), elapsed),)


def _route_candidate(problem, unit, mask, exact_order=True):
    tasks = problem.tasks
    required, produced = _task_vectors(tasks, mask)
    carried = _inventory_vector(unit.inventory)
    pickup = tuple(max(0, need - have) for need, have in zip(required, carried, strict=True))
    inventory_after = tuple(
        have + extra - need + made
        for have, extra, need, made in zip(
            carried,
            pickup,
            required,
            produced,
            strict=True,
        )
    )
    pickup_items = tuple(
        (SHED_ITEMS[index], quantity)
        for index, quantity in enumerate(pickup)
        if quantity
    )
    final_items = tuple(
        (SHED_ITEMS[index], quantity)
        for index, quantity in enumerate(inventory_after)
        if quantity
    )
    accesses = _shed_access(problem.board_size)
    starts = accesses if pickup_items else (unit.position,)
    variants = []
    for start in starts:
        pickup_elapsed = _distance(unit.position, start) + len(pickup_items)
        if exact_order:
            ordered_routes = _best_task_orders(tasks, mask, start, pickup_elapsed)
        else:
            ordered_routes = _heuristic_task_order(
                tasks,
                mask,
                start,
                pickup_elapsed,
            )
        if ordered_routes is None:
            continue
        for order, elapsed in ordered_routes:
            last = start if not order else tasks[order[-1]].position
            end = min(
                accesses,
                key=lambda position: (
                    _distance(last, position),
                    accesses.index(position),
                ),
            )
            total = elapsed
            if final_items:
                total += _distance(last, end) + 1
            if total > problem.max_commands_per_unit:
                continue
            variants.append(
                (
                    total,
                    tuple(tasks[index].identifier for index in order),
                    start,
                    end,
                    order,
                )
            )
    if not variants:
        return None
    _total, _ids, pickup_position, drop_position, order = min(variants)
    commands = []
    current = unit.position
    if pickup_items:
        current = _append_path(commands, current, pickup_position, "@shed-pickup")
        for item, quantity in pickup_items:
            effect = canonical_sha256(
                "offline-route-pickup-effect",
                (unit.identifier, item, quantity, current),
            )
            commands.append(
                _command(
                    current,
                    ("PICKUP", item, quantity),
                    current,
                    f"@pickup:{item}",
                    effect,
                )
            )
    for task_index in order:
        task = tasks[task_index]
        current = _append_path(commands, current, task.position, task.identifier)
        commands.append(
            _command(
                current,
                task.action,
                current,
                task.identifier,
                task.effect_fingerprint,
            )
        )
    if final_items:
        current = _append_path(commands, current, drop_position, "@shed-drop")
        effect = canonical_sha256(
            "offline-route-drop-effect",
            (unit.identifier, final_items, current),
        )
        commands.append(
            _command(current, ("DROP",), current, "@drop", effect)
        )
    movement = sum(command.action[0] in MOVES for command in commands)
    route = UnitRoute(
        unit.identifier,
        tuple(tasks[index].identifier for index in order),
        tuple(commands),
        movement,
        len(commands) - movement,
        pickup_items,
        final_items,
    )
    return _RouteCandidate(mask, route, pickup, inventory_after)


def _stock_fits(problem, pickup, final):
    shed = _inventory_vector(problem.shed)
    if not _pickup_fits(problem, pickup):
        return False
    if sum(shed) + sum(final) > problem.shed_capacity:
        return False
    final_shed = _vector_add(_vector_subtract(shed, pickup), final)
    return sum(final_shed) <= problem.shed_capacity


def _pickup_fits(problem, pickup):
    shed = _inventory_vector(problem.shed)
    return not any(
        used > available for used, available in zip(pickup, shed, strict=True)
    )


def _candidate_score(candidates):
    routes = tuple(candidate.route for candidate in candidates)
    return (
        sum(route.command_count for route in routes),
        max((route.command_count for route in routes), default=0),
        tuple(route.task_identifiers for route in routes),
        tuple(tuple(command.action for command in route.commands) for route in routes),
    )


def _exact_assignment(problem, component_masks):
    full = (1 << len(problem.tasks)) - 1
    caches = [{} for _unit in problem.units]

    def candidate(unit_index, mask):
        cached = caches[unit_index].get(mask)
        if cached is None and mask not in caches[unit_index]:
            cached = _route_candidate(problem, problem.units[unit_index], mask)
            caches[unit_index][mask] = cached
        return cached

    zero = (0,) * len(SHED_ITEMS)
    states = {(0, zero, zero): ()}
    for unit_index in range(len(problem.units)):
        updated = {}
        for (covered, used, final), chosen in states.items():
            remaining = full ^ covered
            subset = remaining
            while True:
                if _is_component_closed(subset, component_masks):
                    route = candidate(unit_index, subset)
                    if route is not None:
                        new_used = _vector_add(used, route.pickup_vector)
                        new_final = _vector_add(final, route.final_vector)
                        if _stock_fits(problem, new_used, new_final):
                            key = (covered | subset, new_used, new_final)
                            value = (*chosen, route)
                            previous = updated.get(key)
                            if previous is None or _candidate_score(value) < _candidate_score(previous):
                                updated[key] = value
                if subset == 0:
                    break
                subset = (subset - 1) & remaining
        states = updated
    complete = [
        chosen
        for (covered, used, final), chosen in states.items()
        if covered == full and _stock_fits(problem, used, final)
    ]
    if not complete:
        return None
    return min(complete, key=_candidate_score)


def _heuristic_assignment(problem, component_masks):
    masks = [0] * len(problem.units)
    candidates = [
        _route_candidate(problem, unit, 0, False)
        for unit in problem.units
    ]
    if any(candidate is None for candidate in candidates):
        return None
    ordered_components = sorted(
        component_masks,
        key=lambda mask: (
            min(
                problem.tasks[index].deadline_turn
                for index in range(len(problem.tasks))
                if mask & (1 << index)
            ),
            min(
                problem.tasks[index].priority
                for index in range(len(problem.tasks))
                if mask & (1 << index)
            ),
            tuple(
                problem.tasks[index].identifier
                for index in range(len(problem.tasks))
                if mask & (1 << index)
            ),
        ),
    )
    for component in ordered_components:
        choices = []
        for unit_index, unit in enumerate(problem.units):
            route = _route_candidate(
                problem,
                unit,
                masks[unit_index] | component,
                False,
            )
            if route is None:
                continue
            trial = list(candidates)
            trial[unit_index] = route
            used = (0,) * len(SHED_ITEMS)
            final = (0,) * len(SHED_ITEMS)
            for current in trial:
                used = _vector_add(used, current.pickup_vector)
                final = _vector_add(final, current.final_vector)
            if not _pickup_fits(problem, used):
                continue
            choices.append(
                (
                    route.route.command_count - candidates[unit_index].route.command_count,
                    route.route.command_count,
                    unit.identifier,
                    unit_index,
                    route,
                )
            )
        if not choices:
            return None
        _delta, _load, _identifier, selected, route = min(choices)
        masks[selected] |= component
        candidates[selected] = route
    used = (0,) * len(SHED_ITEMS)
    final = (0,) * len(SHED_ITEMS)
    for current in candidates:
        used = _vector_add(used, current.pickup_vector)
        final = _vector_add(final, current.final_vector)
    if not _stock_fits(problem, used, final):
        return None
    return tuple(candidates)


def _build_plan(problem, candidates, optimal):
    routes = tuple(candidate.route for candidate in candidates)
    total_movement = sum(route.movement_count for route in routes)
    total_actions = sum(route.action_count for route in routes)
    data = (
        problem.fingerprint,
        problem.route_precondition_fingerprint,
        tuple(
            (
                route.unit_identifier,
                route.task_identifiers,
                tuple(
                    (
                        command.identifier,
                        command.expected_pre_position,
                        command.action,
                        command.expected_post_position,
                        command.task_identifier,
                        command.effect_fingerprint,
                    )
                    for command in route.commands
                ),
            )
            for route in routes
        ),
        optimal,
        COLLISION_POLICY,
    )
    return RoutePlan(
        problem.fingerprint,
        problem.route_precondition_fingerprint,
        routes,
        total_movement + total_actions,
        total_movement,
        total_actions,
        max((route.command_count for route in routes), default=0),
        optimal,
        COLLISION_POLICY,
        canonical_sha256("offline-route-plan", data),
    )


def plan_routes(problem):
    if type(problem) is not RouteProblem:
        raise TypeError("problem has wrong type")
    components = _component_masks(problem.tasks)
    if len(problem.tasks) <= EXACT_TASK_LIMIT:
        candidates = _exact_assignment(problem, components)
        optimal = True
    else:
        candidates = _heuristic_assignment(problem, components)
        optimal = False
    if candidates is None:
        return RouteFailure("solve", "no complete feasible route", problem.fingerprint)
    plan = _build_plan(problem, candidates, optimal)
    errors = verify_plan(problem, plan)
    if errors:
        return RouteFailure("verify", "; ".join(errors), problem.fingerprint)
    return plan


def verify_plan(problem, plan):
    errors = []
    if type(problem) is not RouteProblem or type(plan) is not RoutePlan:
        return ("wrong verifier input type",)
    if plan.problem_fingerprint != problem.fingerprint:
        errors.append("problem fingerprint mismatch")
    if plan.route_precondition_fingerprint != problem.route_precondition_fingerprint:
        errors.append("route precondition mismatch")
    if plan.collision_policy != COLLISION_POLICY:
        errors.append("collision policy mismatch")
    if tuple(route.unit_identifier for route in plan.routes) != tuple(
        unit.identifier for unit in problem.units
    ):
        errors.append("unit route order mismatch")
        return tuple(errors)
    task_by_id = {task.identifier: task for task in problem.tasks}
    task_index = {
        task.identifier: index for index, task in enumerate(problem.tasks)
    }
    completed = {}
    seen_tasks = []
    shed = list(_inventory_vector(problem.shed))
    positions = [unit.position for unit in problem.units]
    inventories = [list(_inventory_vector(unit.inventory)) for unit in problem.units]
    route_seen = [[] for _route in plan.routes]
    route_movement = [0 for _route in plan.routes]
    route_actions = [0 for _route in plan.routes]
    route_pickup = [[] for _route in plan.routes]
    total_movement = 0
    total_actions = 0
    maximum_turns = max((len(route.commands) for route in plan.routes), default=0)
    for turn_index in range(maximum_turns):
        for unit_index, route in enumerate(plan.routes):
            if turn_index >= len(route.commands):
                continue
            command = route.commands[turn_index]
            unit = problem.units[unit_index]
            position = positions[unit_index]
            inventory = inventories[unit_index]
            if command.expected_pre_position != position:
                errors.append(f"{unit.identifier}: command pre-position mismatch")
                continue
            operation = command.action[0]
            expected_effect = None
            if operation in MOVES:
                dx, dy = MOVES[operation]
                position = (position[0] + dx, position[1] + dy)
                expected_effect = canonical_sha256(
                    "offline-route-move-effect",
                    (
                        command.expected_pre_position,
                        position,
                        command.task_identifier,
                    ),
                )
                total_movement += 1
                route_movement[unit_index] += 1
            else:
                total_actions += 1
                route_actions[unit_index] += 1
                if operation == "PICKUP":
                    if position not in _shed_access(problem.board_size):
                        errors.append(f"{unit.identifier}: pickup away from shed")
                    item, quantity = command.action[1:]
                    item_index = SHED_ITEMS.index(item)
                    expected_effect = canonical_sha256(
                        "offline-route-pickup-effect",
                        (unit.identifier, item, quantity, position),
                    )
                    if shed[item_index] < quantity:
                        errors.append(f"{unit.identifier}: pickup exceeds shed stock")
                    else:
                        shed[item_index] -= quantity
                        inventory[item_index] += quantity
                        route_pickup[unit_index].append((item, quantity))
                elif operation == "DROP":
                    if position not in _shed_access(problem.board_size):
                        errors.append(f"{unit.identifier}: drop away from shed")
                    dropped = tuple(
                        (SHED_ITEMS[item_index], quantity)
                        for item_index, quantity in enumerate(inventory)
                        if quantity
                    )
                    expected_effect = canonical_sha256(
                        "offline-route-drop-effect",
                        (unit.identifier, dropped, position),
                    )
                    for item_index, quantity in enumerate(inventory):
                        shed[item_index] += quantity
                        inventory[item_index] = 0
                    if sum(shed) > problem.shed_capacity:
                        errors.append(f"{unit.identifier}: shed overflow")
                elif command.task_identifier in task_by_id:
                    task = task_by_id[command.task_identifier]
                    expected_effect = task.effect_fingerprint
                    if command.action != task.action or position != task.position:
                        errors.append(f"{task.identifier}: task command mismatch")
                    for dependency in task.dependencies:
                        if dependency not in completed:
                            errors.append(f"{task.identifier}: dependency incomplete")
                    for item, quantity in task.requires:
                        item_index = SHED_ITEMS.index(item)
                        if inventory[item_index] < quantity:
                            errors.append(f"{task.identifier}: missing carried item")
                        else:
                            inventory[item_index] -= quantity
                    for item, quantity in task.produces:
                        inventory[SHED_ITEMS.index(item)] += quantity
                    if turn_index + 1 > task.deadline_turn:
                        errors.append(f"{task.identifier}: deadline missed")
                    if command.effect_fingerprint != task.effect_fingerprint:
                        errors.append(f"{task.identifier}: effect mismatch")
                    completed[task.identifier] = (unit.identifier, turn_index + 1)
                    seen_tasks.append(task.identifier)
                    route_seen[unit_index].append(task.identifier)
                else:
                    errors.append(f"{unit.identifier}: unknown synthetic action")
            if expected_effect is not None and command.effect_fingerprint != expected_effect:
                errors.append(f"{unit.identifier}: synthetic effect mismatch")
            expected_identifier = _command(
                command.expected_pre_position,
                command.action,
                command.expected_post_position,
                command.task_identifier,
                command.effect_fingerprint,
            ).identifier
            if command.identifier != expected_identifier:
                errors.append(f"{unit.identifier}: command identifier mismatch")
            if command.expected_post_position != position:
                errors.append(f"{unit.identifier}: command post-position mismatch")
            positions[unit_index] = position
    for unit_index, (unit, route) in enumerate(
        zip(problem.units, plan.routes, strict=True)
    ):
        if tuple(route_seen[unit_index]) != route.task_identifiers:
            errors.append(f"{unit.identifier}: task order mismatch")
        if len(route.commands) > problem.max_commands_per_unit:
            errors.append(f"{unit.identifier}: command budget exceeded")
        if route.movement_count != route_movement[unit_index]:
            errors.append(f"{unit.identifier}: movement count mismatch")
        if route.action_count != route_actions[unit_index]:
            errors.append(f"{unit.identifier}: action count mismatch")
        if tuple(route_pickup[unit_index]) != route.pickup:
            errors.append(f"{unit.identifier}: pickup declaration mismatch")
        required, produced = _task_vectors(
            problem.tasks,
            sum(
                1 << task_index[identifier]
                for identifier in route.task_identifiers
            ),
        )
        projected = tuple(
            have + picked - need + made
            for have, picked, need, made in zip(
                _inventory_vector(unit.inventory),
                _inventory_vector(route.pickup),
                required,
                produced,
                strict=True,
            )
        )
        projected_entries = tuple(
            (SHED_ITEMS[index], quantity)
            for index, quantity in enumerate(projected)
            if quantity
        )
        if projected_entries != route.final_inventory:
            errors.append(f"{unit.identifier}: final inventory declaration mismatch")
        actual_entries = tuple(
            (SHED_ITEMS[index], quantity)
            for index, quantity in enumerate(inventories[unit_index])
            if quantity
        )
        if actual_entries:
            errors.append(f"{unit.identifier}: final inventory mismatch")
    if sorted(seen_tasks) != sorted(task_by_id):
        errors.append("task coverage mismatch")
    if len(seen_tasks) != len(set(seen_tasks)):
        errors.append("task executed more than once")
    if total_movement != plan.total_movement:
        errors.append("movement total mismatch")
    if total_actions != plan.total_actions:
        errors.append("action total mismatch")
    if total_movement + total_actions != plan.total_cost:
        errors.append("cost total mismatch")
    if max((route.command_count for route in plan.routes), default=0) != plan.maximum_unit_load:
        errors.append("maximum load mismatch")
    rebuilt = _build_plan(
        problem,
        tuple(
            _RouteCandidate(
                0,
                route,
                _inventory_vector(route.pickup),
                _inventory_vector(route.final_inventory),
            )
            for route in plan.routes
        ),
        plan.optimal,
    )
    if rebuilt.fingerprint != plan.fingerprint:
        errors.append("plan fingerprint mismatch")
    return tuple(errors)


class RouteExecutor:
    def __init__(self):
        self._plan = None
        self._cursors = ()
        self._pending = None

    @property
    def plan(self):
        return self._plan

    @property
    def cursors(self):
        return self._cursors

    def reset(self):
        self._plan = None
        self._cursors = ()
        self._pending = None

    def prepare(self, problem):
        if (
            self._plan is not None
            and self._plan.problem_fingerprint == problem.fingerprint
            and self._plan.route_precondition_fingerprint
            == problem.route_precondition_fingerprint
        ):
            return self._plan
        result = plan_routes(problem)
        if type(result) is RouteFailure:
            self.reset()
            return result
        self._plan = result
        self._cursors = (0,) * len(result.routes)
        self._pending = None
        return result

    def next_actions(self, positions):
        if self._plan is None:
            return RouteFailure("dispatch", "no executable route", None)
        if self._pending is not None:
            return RouteFailure(
                "dispatch",
                "previous commands are not acknowledged",
                self._plan.problem_fingerprint,
            )
        if type(positions) is not tuple or len(positions) != len(self._plan.routes):
            self.reset()
            return RouteFailure("dispatch", "position count mismatch", None)
        actions = []
        pending = []
        for route, cursor, position in zip(
            self._plan.routes,
            self._cursors,
            positions,
            strict=True,
        ):
            if cursor >= len(route.commands):
                actions.append((route.unit_identifier, ("PASS",)))
                continue
            command = route.commands[cursor]
            if position != command.expected_pre_position:
                fingerprint = self._plan.problem_fingerprint
                self.reset()
                return RouteFailure("dispatch", "unit position diverged", fingerprint)
            actions.append((route.unit_identifier, command.action))
            pending.append((route.unit_identifier, command))
        self._pending = tuple(pending)
        return tuple(actions)

    def acknowledge(self, command_identifiers, positions, route_precondition_fingerprint):
        if self._plan is None or self._pending is None:
            return RouteFailure("acknowledge", "no pending commands", None)
        if route_precondition_fingerprint != self._plan.route_precondition_fingerprint:
            fingerprint = self._plan.problem_fingerprint
            self.reset()
            return RouteFailure("acknowledge", "route precondition changed", fingerprint)
        expected_ids = tuple(command.identifier for _unit, command in self._pending)
        if command_identifiers != expected_ids:
            fingerprint = self._plan.problem_fingerprint
            self.reset()
            return RouteFailure("acknowledge", "command acknowledgement mismatch", fingerprint)
        expected_positions = tuple(
            next(
                command.expected_post_position
                for pending_unit, command in self._pending
                if pending_unit == route.unit_identifier
            )
            if any(pending_unit == route.unit_identifier for pending_unit, _command in self._pending)
            else positions[index]
            for index, route in enumerate(self._plan.routes)
        )
        if positions != expected_positions:
            fingerprint = self._plan.problem_fingerprint
            self.reset()
            return RouteFailure("acknowledge", "post-position mismatch", fingerprint)
        cursor_by_unit = {
            unit_identifier
            for unit_identifier, _command in self._pending
        }
        self._cursors = tuple(
            cursor + (route.unit_identifier in cursor_by_unit)
            for route, cursor in zip(self._plan.routes, self._cursors, strict=True)
        )
        self._pending = None
        return self._plan
