import time
from dataclasses import dataclass

from .tasks import TaskGraph, TaskId, TaskNode


@dataclass(frozen=True, slots=True)
class SelectorRecord:
    unit_index: int
    selected_index: int
    candidates: tuple
    taken: tuple[int, ...]
    targets: tuple


@dataclass(frozen=True, slots=True)
class UnitRoute:
    unit_index: int
    head: TaskId
    suffix: tuple[TaskId, ...]

    @property
    def goals(self) -> tuple[TaskId, ...]:
        return (self.head, *self.suffix)


@dataclass(frozen=True, slots=True)
class TurnPlan:
    step: int
    day: int
    routes: tuple[UnitRoute, ...]
    selector_calls: int
    queued_tasks: int
    unassigned_indices: tuple[int, ...]
    build_ns: int
    unit_count: int


@dataclass(frozen=True, slots=True)
class PersistentDiagnostics:
    persisted_selections: int
    priority_pauses: int
    safety_pauses: int
    frozen_fallbacks: int
    invalidations: tuple[tuple[str, int], ...]
    turns_with_persistence: int
    absent_goals: int
    unavailable_fallbacks: int
    unrouted_fallbacks: int
    unit_growths: int


@dataclass(frozen=True, slots=True)
class StickyDiagnostics:
    persisted_selections: int
    priority_pauses: int
    safety_pauses: int
    frozen_fallbacks: int
    turns_with_persistence: int
    absent_goals: int
    unavailable_fallbacks: int
    unrouted_fallbacks: int
    unit_growths: int
    enrollments: int
    day_resets: int


class RebuildQueueScheduler:
    def __init__(self):
        self.plan = None
        self.rebuild_count = 0
        self.max_build_ns = 0
        self._clear_turn()

    def reset(self) -> None:
        self.plan = None
        self.rebuild_count = 0
        self.max_build_ns = 0
        self._clear_turn()

    def begin(self, obs) -> None:
        self.plan = None
        self._clear_turn()
        self._active = True
        self._step = int(obs.get("step", obs["day"] * 24 + obs["hour"]))
        self._day = int(obs["day"])
        self._player = int(obs["player"])

    def capture(self, graph, protected, units, inventories) -> None:
        if not self._active:
            return
        self._graph = graph
        self._protected = tuple(sorted(protected.items()))
        self._positions = tuple(tuple(position) for position in units)
        self._inventories = tuple(
            tuple(sorted(dict(inventory).items())) for inventory in inventories
        )

    def record_selector(
        self,
        unit_index,
        selected_index,
        candidates,
        taken,
        targets,
    ) -> None:
        if not self._active:
            return
        frozen_candidates = tuple(
            tuple(value) if not isinstance(value, tuple) else value
            for value in candidates
        )
        frozen_targets = tuple(
            sorted((int(index), tuple(position)) for index, position in targets.items())
        )
        self._records.append(
            SelectorRecord(
                int(unit_index),
                int(selected_index),
                frozen_candidates,
                tuple(sorted(int(index) for index in taken)),
                frozen_targets,
            )
        )

    def finish(self, graph: TaskGraph, module) -> TurnPlan:
        started = time.perf_counter_ns()
        if self._graph is None:
            self._graph = graph
        routes, assigned = self._build_routes(module)
        all_indices = tuple(node.source_order for node in self._graph.nodes)
        unassigned = tuple(index for index in all_indices if index not in assigned)
        build_ns = time.perf_counter_ns() - started
        plan = TurnPlan(
            self._step,
            self._day,
            routes,
            len(self._records),
            sum(len(route.goals) for route in routes),
            unassigned,
            build_ns,
            len(self._positions),
        )
        self.plan = plan
        self.rebuild_count += 1
        self.max_build_ns = max(self.max_build_ns, build_ns)
        self._clear_turn()
        return plan

    def abort(self) -> None:
        self.plan = None
        self._clear_turn()

    def _clear_turn(self) -> None:
        self._active = False
        self._step = 0
        self._day = 0
        self._player = 0
        self._graph = None
        self._protected = ()
        self._positions = ()
        self._inventories = ()
        self._records = []

    def _build_routes(self, module):
        nodes = {node.source_order: node for node in self._graph.nodes}
        assigned = set()
        route_nodes = {}
        records = []
        for record in self._records:
            node = nodes.get(record.selected_index)
            if node is None or node.source_order in assigned:
                continue
            assigned.add(node.source_order)
            route_nodes[record.unit_index] = [node]
            records.append(record)
        if not records:
            return (), assigned
        protected = dict(self._protected)
        zones = self._zones(module)
        while True:
            added = False
            for record in records:
                unit_index = record.unit_index
                route = route_nodes[unit_index]
                inventory = self._inventory(unit_index)
                node = self._choose_suffix(
                    module,
                    route[-1],
                    unit_index,
                    inventory,
                    zones,
                    protected,
                    assigned,
                )
                if node is None:
                    continue
                assigned.add(node.source_order)
                route.append(node)
                added = True
            if not added:
                break
        routes = tuple(
            UnitRoute(
                record.unit_index,
                route_nodes[record.unit_index][0].identifier,
                tuple(node.identifier for node in route_nodes[record.unit_index][1:]),
            )
            for record in records
        )
        return routes, assigned

    def _zones(self, module):
        state = module._day_plans.get(self._player) or {}
        return dict(state.get("zones", {}))

    def _inventory(self, unit_index):
        if unit_index >= len(self._inventories):
            return {}
        return dict(self._inventories[unit_index])

    def _choose_suffix(
        self,
        module,
        tail,
        unit_index,
        inventory,
        zones,
        protected,
        assigned,
    ):
        candidates = []
        for node in self._graph.nodes:
            if node.source_order in assigned or node.source_order in protected:
                continue
            if not self._can_do(node, inventory):
                continue
            candidate = self._candidate(module, node, tail, unit_index, zones)
            candidates.append((node, candidate))
        if not candidates:
            return None
        safe_class = min(candidate[-1] for _node, candidate in candidates)
        eligible = [
            (node, candidate)
            for node, candidate in candidates
            if candidate[-1] == safe_class
        ]
        scores = [
            self._score(module, candidate[:-1], unit_index, zones, assigned)
            for _node, candidate in eligible
        ]
        selected = max(range(len(scores)), key=lambda index: scores[index])
        return eligible[selected][0]

    def _candidate(self, module, node, tail, unit_index, zones):
        raw_distance = abs(tail.x - node.x) + abs(tail.y - node.y)
        distance = raw_distance
        zone = zones.get((node.x, node.y))
        if zone is not None and zone != unit_index and node.priority > 0:
            distance += module.ZONE_PENALTY
        key = module._task_key(node.priority, distance)
        if module.UNDERFOOT and distance == 0 and node.priority > 0:
            key = (0, 1, 0)
        if node.priority == 0:
            safe_class = (0, 0)
        elif key[0] == 0:
            safe_class = (0, 1)
        else:
            safe_class = (key[0], 0)
        return (
            node.source_order,
            node.priority,
            node.x,
            node.y,
            (node.operation, node.item),
            raw_distance,
            distance,
            zone,
            safe_class,
        )

    def _score(self, module, candidate, unit_index, zones, assigned):
        task_index, priority, x, y, task, raw_distance, distance, zone = candidate
        continuations = [
            abs(x - node.x) + abs(y - node.y)
            for node in self._graph.nodes
            if node.source_order != task_index and node.source_order not in assigned
        ]
        continuation = min(continuations, default=0)
        density = sum(value <= 2 for value in continuations)
        in_zone = 1.0 if zone in (None, unit_index) else 0.0
        same_target = 0.0
        if module.ROUTE_RL_MODE == "basic":
            in_zone = 0.0
        task_features = [
            1.0 if task[0] == operation else 0.0
            for operation in module.ROUTE_RL_TASKS
        ]
        features = (
            -float(priority),
            -float(raw_distance),
            -float(distance),
            -float(continuation),
            float(density),
            in_zone,
            same_target,
            *task_features,
        )
        return sum(
            weight * value for weight, value in zip(module.ROUTE_RL_WEIGHTS, features)
        )

    @staticmethod
    def _can_do(node: TaskNode, inventory) -> bool:
        if node.operation in ("FEED", "FEED!"):
            return inventory.get("WHEAT", 0) > 0
        if node.operation == "FERTILIZE":
            return inventory.get("FERTILIZER", 0) > 0
        if node.operation == "PLACE":
            return inventory.get(node.item, 0) > 0
        return True


class LocalFallbackQueueScheduler(RebuildQueueScheduler):
    def __init__(self):
        super().__init__()
        self._reset_diagnostics()
        self._committed_plan = None

    @property
    def diagnostics(self) -> PersistentDiagnostics:
        return PersistentDiagnostics(
            self._persisted_selections,
            self._priority_pauses,
            self._safety_pauses,
            self._frozen_fallbacks,
            tuple(sorted(self._invalidations.items())),
            self._turns_with_persistence,
            self._absent_goals,
            self._unavailable_fallbacks,
            self._unrouted_fallbacks,
            self._unit_growths,
        )

    def reset(self) -> None:
        super().reset()
        self._reset_diagnostics()
        self._committed_plan = None

    def begin(self, obs) -> None:
        self._committed_plan = self.plan
        self._clear_turn()
        self._active = True
        self._step = int(obs.get("step", obs["day"] * 24 + obs["hour"]))
        self._day = int(obs["day"])
        self._player = int(obs["player"])
        self._mapped_routes = ()
        self._mapped_plan = None
        self._rebuild_required = self._committed_plan is None
        self._rebuild_reason = "initial" if self._rebuild_required else None
        self._pending = {
            "persisted": 0,
            "priority": 0,
            "safety": 0,
            "fallback": 0,
            "absent": 0,
            "unavailable": 0,
            "unrouted": 0,
            "growth": 0,
        }

    def capture(self, graph, protected, units, inventories) -> None:
        super().capture(graph, protected, units, inventories)
        plan = self._committed_plan
        if plan is None:
            return
        if plan.day != self._day:
            self._require_rebuild("day-change")
            return
        if plan.unit_count > len(units):
            self._require_rebuild("unit-removed")
            return
        if plan.unit_count < len(units):
            self._pending["growth"] += len(units) - plan.unit_count
        routes = []
        absent = 0
        for route in plan.routes:
            goals = tuple(goal for goal in route.goals if graph.find(goal) is not None)
            absent += len(route.goals) - len(goals)
            if goals:
                routes.append(UnitRoute(route.unit_index, goals[0], goals[1:]))
        self._mapped_routes = tuple(routes)
        self._pending["absent"] += absent
        if graph.nodes and not self._mapped_routes:
            self._require_rebuild("all-routes-empty")

    def choose(
        self,
        unit_index,
        candidates,
        taken,
        original_index,
        training=False,
    ):
        if not self._active or self._graph is None:
            return original_index
        if self._rebuild_required:
            self._pending["fallback"] += 1
            return original_index
        route = next(
            (route for route in self._mapped_routes if route.unit_index == unit_index),
            None,
        )
        if route is None:
            self._pending["fallback"] += 1
            self._pending["unrouted"] += 1
            return original_index
        goal = route.head
        node = self._graph.find(goal)
        by_index = {candidate[0]: candidate for candidate in candidates}
        original = by_index[original_index]
        persisted = by_index.get(node.source_order) if node is not None else None
        if persisted is None or node.source_order in taken:
            self._pending["fallback"] += 1
            self._pending["unavailable"] += 1
            return original_index
        if training or original[1] == 0:
            self._pending["priority"] += 1
            return original_index
        if persisted[-1] != original[-1]:
            self._pending["safety"] += 1
            return original_index
        if persisted[1] != original[1]:
            self._pending["priority"] += 1
            return original_index
        if persisted[0] == original_index:
            return original_index
        self._pending["persisted"] += 1
        return persisted[0]

    def finish(self, graph: TaskGraph, module) -> TurnPlan:
        if self._rebuild_required:
            reason = self._rebuild_reason
            plan = super().finish(graph, module)
            if reason != "initial":
                self._invalidations[reason] = self._invalidations.get(reason, 0) + 1
        else:
            assigned = {
                node.source_order
                for route in self._mapped_routes
                for goal in route.goals
                for node in (graph.find(goal),)
                if node is not None
            }
            unassigned = tuple(
                node.source_order for node in graph.nodes
                if node.source_order not in assigned
            )
            plan = TurnPlan(
                self._step,
                self._day,
                self._mapped_routes,
                len(self._records),
                sum(len(route.goals) for route in self._mapped_routes),
                unassigned,
                0,
                len(self._positions),
            )
            self.plan = plan
            self._clear_turn()
        self._commit_pending()
        self._committed_plan = None
        return plan

    def abort(self) -> None:
        self.plan = self._committed_plan
        self._committed_plan = None
        self._clear_turn()

    def _require_rebuild(self, reason) -> None:
        if self._rebuild_required:
            return
        self._rebuild_required = True
        self._rebuild_reason = reason

    def _commit_pending(self) -> None:
        self._persisted_selections += self._pending["persisted"]
        self._priority_pauses += self._pending["priority"]
        self._safety_pauses += self._pending["safety"]
        self._frozen_fallbacks += self._pending["fallback"]
        self._absent_goals += self._pending["absent"]
        self._unavailable_fallbacks += self._pending["unavailable"]
        self._unrouted_fallbacks += self._pending["unrouted"]
        self._unit_growths += self._pending["growth"]
        if self._pending["persisted"]:
            self._turns_with_persistence += 1

    def _reset_diagnostics(self) -> None:
        self._persisted_selections = 0
        self._priority_pauses = 0
        self._safety_pauses = 0
        self._frozen_fallbacks = 0
        self._invalidations = {}
        self._turns_with_persistence = 0
        self._absent_goals = 0
        self._unavailable_fallbacks = 0
        self._unrouted_fallbacks = 0
        self._unit_growths = 0


class IndexedStickyGoalScheduler(RebuildQueueScheduler):
    def __init__(self):
        super().__init__()
        self._committed_plan = None
        self._reset_sticky_diagnostics()

    @property
    def diagnostics(self) -> StickyDiagnostics:
        return StickyDiagnostics(
            self._persisted_selections,
            self._priority_pauses,
            self._safety_pauses,
            self._frozen_fallbacks,
            self._turns_with_persistence,
            self._absent_goals,
            self._unavailable_fallbacks,
            self._unrouted_fallbacks,
            self._unit_growths,
            self._enrollment_count,
            self._day_resets,
        )

    @property
    def enrollment_count(self) -> int:
        return self._enrollment_count

    def reset(self) -> None:
        super().reset()
        self._committed_plan = None
        self._reset_sticky_diagnostics()

    def begin(self, obs) -> None:
        self._committed_plan = self.plan
        self._clear_turn()
        self._active = True
        self._step = int(obs.get("step", obs["day"] * 24 + obs["hour"]))
        self._day = int(obs["day"])
        self._player = int(obs["player"])
        self._mapped_routes = ()
        self._routes_by_unit = {}
        self._nodes_by_identifier = {}
        self._nodes_by_source = {}
        self._selector_calls = 0
        self._unit_count = 0
        self._enrollments = {}
        self._pending = {
            "persisted": 0,
            "priority": 0,
            "safety": 0,
            "fallback": 0,
            "absent": 0,
            "unavailable": 0,
            "unrouted": 0,
            "growth": 0,
            "enrollments": 0,
            "day_resets": 0,
        }

    def capture(self, graph, protected, units, inventories) -> None:
        if not self._active:
            return
        self._graph = graph
        self._unit_count = len(units)
        self._nodes_by_identifier = {
            node.identifier: node for node in graph.nodes
        }
        self._nodes_by_source = {
            node.source_order: node for node in graph.nodes
        }
        plan = self._committed_plan
        if plan is None:
            return
        if plan.day != self._day:
            self._pending["day_resets"] = 1
            return
        if plan.unit_count < len(units):
            self._pending["growth"] += len(units) - plan.unit_count
        routes = []
        absent = 0
        for route in plan.routes:
            if route.unit_index >= len(units):
                absent += 1
                continue
            node = self._nodes_by_identifier.get(route.head)
            if node is None:
                absent += 1
                continue
            routes.append(UnitRoute(route.unit_index, route.head, ()))
        self._mapped_routes = tuple(routes)
        self._routes_by_unit = {
            route.unit_index: route for route in self._mapped_routes
        }
        self._pending["absent"] += absent

    def choose(
        self,
        unit_index,
        candidates,
        taken,
        original_index,
        training=False,
    ):
        if not self._active or self._graph is None:
            return original_index
        route = self._route(unit_index)
        if route is None:
            self._pending["fallback"] += 1
            self._pending["unrouted"] += 1
            return original_index
        node = self._nodes_by_identifier.get(route.head)
        original = None
        persisted = None
        goal_index = node.source_order if node is not None else None
        for candidate in candidates:
            if candidate[0] == original_index:
                original = candidate
            if candidate[0] == goal_index:
                persisted = candidate
        if persisted is None or node.source_order in taken:
            self._pending["fallback"] += 1
            self._pending["unavailable"] += 1
            return original_index
        if training or original[1] == 0:
            self._pending["priority"] += 1
            return original_index
        if persisted[-1] != original[-1]:
            self._pending["safety"] += 1
            return original_index
        if persisted[1] != original[1]:
            self._pending["priority"] += 1
            return original_index
        if persisted[0] == original_index:
            return original_index
        self._pending["persisted"] += 1
        return persisted[0]

    def record_selector(
        self,
        unit_index,
        selected_index,
        candidates,
        taken,
        targets,
    ) -> None:
        if not self._active:
            return
        self._selector_calls += 1
        if self._route(unit_index) is not None or unit_index in self._enrollments:
            return
        node = self._nodes_by_source.get(selected_index)
        if node is None:
            return
        self._enrollments[unit_index] = UnitRoute(unit_index, node.identifier, ())
        self._pending["enrollments"] += 1

    def finish(self, graph: TaskGraph, module) -> TurnPlan:
        if self._graph is None:
            self._graph = graph
        routes = dict(self._routes_by_unit)
        routes.update(self._enrollments)
        ordered = tuple(routes[index] for index in sorted(routes))
        plan = TurnPlan(
            self._step,
            self._day,
            ordered,
            self._selector_calls,
            len(ordered),
            (),
            0,
            self._unit_count,
        )
        self.plan = plan
        self._commit_sticky_pending()
        self._committed_plan = None
        self._clear_turn()
        return plan

    def abort(self) -> None:
        self.plan = self._committed_plan
        self._committed_plan = None
        self._clear_turn()

    def _route(self, unit_index):
        return self._routes_by_unit.get(unit_index)

    def _commit_sticky_pending(self) -> None:
        self._persisted_selections += self._pending["persisted"]
        self._priority_pauses += self._pending["priority"]
        self._safety_pauses += self._pending["safety"]
        self._frozen_fallbacks += self._pending["fallback"]
        self._absent_goals += self._pending["absent"]
        self._unavailable_fallbacks += self._pending["unavailable"]
        self._unrouted_fallbacks += self._pending["unrouted"]
        self._unit_growths += self._pending["growth"]
        self._enrollment_count += self._pending["enrollments"]
        self._day_resets += self._pending["day_resets"]
        if self._pending["persisted"]:
            self._turns_with_persistence += 1

    def _reset_sticky_diagnostics(self) -> None:
        self._persisted_selections = 0
        self._priority_pauses = 0
        self._safety_pauses = 0
        self._frozen_fallbacks = 0
        self._turns_with_persistence = 0
        self._absent_goals = 0
        self._unavailable_fallbacks = 0
        self._unrouted_fallbacks = 0
        self._unit_growths = 0
        self._enrollment_count = 0
        self._day_resets = 0
