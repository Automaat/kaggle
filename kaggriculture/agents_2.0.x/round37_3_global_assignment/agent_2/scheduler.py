from collections import deque

from .tasks import TaskNode


class GlobalAssignmentScheduler:
    def __init__(self):
        self.reset()

    def reset(self):
        self.graph = None
        self.protected = {}
        self.positions = ()
        self.inventories = ()
        self.selector_units = set()
        self.player = 0

    def begin(self, obs):
        self.graph = None
        self.protected = {}
        self.positions = ()
        self.inventories = ()
        self.selector_units = set()
        self.player = int(obs["player"])

    def capture(self, graph, protected, units, inventories):
        self.graph = graph
        self.protected = dict(protected)
        self.positions = tuple(tuple(position) for position in units)
        self.inventories = tuple(dict(inventory) for inventory in inventories)

    def record_selector(self, unit_index):
        self.selector_units.add(int(unit_index))

    def rewrite(self, action, module):
        if self.graph is None or not self.selector_units:
            return action
        assignments = self._assign(module)
        rewritten = {
            "farmer": list(action["farmer"]),
            "hands": [list(op) for op in action.get("hands", [])],
            "market": [list(order) for order in action.get("market", [])],
        }
        for unit_index, node in assignments.items():
            position = self.positions[unit_index]
            operation = module._step_toward(position, (node.x, node.y))
            if operation is None:
                operation = module._act(node.operation, node.item)
            if unit_index == 0:
                rewritten["farmer"] = operation
            elif unit_index - 1 < len(rewritten["hands"]):
                rewritten["hands"][unit_index - 1] = operation
        return rewritten

    def _assign(self, module):
        units = tuple(sorted(self.selector_units))
        nodes = tuple(self.graph.nodes)
        if not units or not nodes:
            return {}
        source = 0
        unit_offset = 1
        task_offset = unit_offset + len(units)
        dummy_offset = task_offset + len(nodes)
        sink = dummy_offset + len(units)
        graph = [[] for _ in range(sink + 1)]

        def edge(start, end, capacity, cost):
            graph[start].append([end, len(graph[end]), capacity, cost])
            graph[end].append([start, len(graph[start]) - 1, 0, -cost])

        for row, unit_index in enumerate(units):
            edge(source, unit_offset + row, 1, 0)
            for column, node in enumerate(nodes):
                cost = self._cost(module, unit_index, node)
                if cost is not None:
                    edge(unit_offset + row, task_offset + column, 1, cost)
            edge(unit_offset + row, dummy_offset + row, 1, 99_000_000)
        for column in range(len(nodes)):
            edge(task_offset + column, sink, 1, 0)
        for row in range(len(units)):
            edge(dummy_offset + row, sink, 1, 0)

        for _ in units:
            distance = [None] * len(graph)
            previous = [None] * len(graph)
            queued = [False] * len(graph)
            distance[source] = 0
            queue = deque([source])
            queued[source] = True
            while queue:
                current = queue.popleft()
                queued[current] = False
                for index, candidate in enumerate(graph[current]):
                    target, _reverse, capacity, cost = candidate
                    if capacity <= 0:
                        continue
                    value = distance[current] + cost
                    if distance[target] is None or value < distance[target]:
                        distance[target] = value
                        previous[target] = (current, index)
                        if not queued[target]:
                            queue.append(target)
                            queued[target] = True
            if distance[sink] is None:
                break
            current = sink
            while current != source:
                parent, index = previous[current]
                candidate = graph[parent][index]
                candidate[2] -= 1
                graph[current][candidate[1]][2] += 1
                current = parent

        assignments = {}
        for row, unit_index in enumerate(units):
            for candidate in graph[unit_offset + row]:
                target, _reverse, capacity, _cost = candidate
                if task_offset <= target < dummy_offset and capacity == 0:
                    assignments[unit_index] = nodes[target - task_offset]
                    break
        return assignments

    def _cost(self, module, unit_index, node: TaskNode):
        protected = self.protected.get(node.source_order)
        if protected is not None and protected != unit_index:
            return None
        inventory = self.inventories[unit_index] if unit_index < len(self.inventories) else {}
        if not self._can_do(node, inventory):
            return None
        position = self.positions[unit_index]
        raw_distance = abs(position[0] - node.x) + abs(position[1] - node.y)
        zones = (module._day_plans.get(self.player) or {}).get("zones", {})
        zone = zones.get((node.x, node.y))
        distance = raw_distance
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
        safe_rank = safe_class[0] * 2 + safe_class[1]
        return safe_rank * 1_000_000 + node.priority * 10_000 + distance * 100 + node.source_order

    @staticmethod
    def _can_do(node, inventory):
        if node.operation in ("FEED", "FEED!"):
            return inventory.get("WHEAT", 0) > 0
        if node.operation == "FERTILIZE":
            return inventory.get("FERTILIZER", 0) > 0
        if node.operation == "PLACE":
            return inventory.get(node.item, 0) > 0
        return True
