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
