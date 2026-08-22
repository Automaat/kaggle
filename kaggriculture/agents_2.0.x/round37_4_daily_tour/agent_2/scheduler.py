from .tasks import TaskNode


class DailyTourScheduler:
    def __init__(self):
        self.reset()

    def reset(self):
        self.graph = None
        self.protected = {}
        self.positions = ()
        self.inventories = ()
        self.selector_units = set()
        self.player = 0
        self.board_size = 10

    def begin(self, obs):
        self.graph = None
        self.protected = {}
        self.positions = ()
        self.inventories = ()
        self.selector_units = set()
        self.player = int(obs["player"])
        self.board_size = len(obs["farms"][self.player]["tiles"])

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
        zones = (module._day_plans.get(self.player) or {}).get("zones", {})
        remaining = set(range(len(self.graph.nodes)))
        assignments = {}
        for unit_index in sorted(self.selector_units):
            candidates = []
            for index in remaining:
                node = self.graph.nodes[index]
                if not self._eligible(node, unit_index):
                    continue
                zone = zones.get((node.x, node.y))
                route_index = self._route_index(node.x, node.y)
                position = self.positions[unit_index]
                distance = abs(position[0] - node.x) + abs(position[1] - node.y)
                key = (
                    0 if node.priority == 0 else 1,
                    0 if zone in (None, unit_index) else 1,
                    route_index,
                    node.priority,
                    distance,
                    node.source_order,
                )
                candidates.append((key, index, node))
            if not candidates:
                continue
            _key, index, node = min(candidates)
            remaining.remove(index)
            assignments[unit_index] = node
        return assignments

    def _eligible(self, node: TaskNode, unit_index):
        protected = self.protected.get(node.source_order)
        if protected is not None and protected != unit_index:
            return False
        inventory = self.inventories[unit_index] if unit_index < len(self.inventories) else {}
        if node.operation in ("FEED", "FEED!"):
            return inventory.get("WHEAT", 0) > 0
        if node.operation == "FERTILIZE":
            return inventory.get("FERTILIZER", 0) > 0
        if node.operation == "PLACE":
            return inventory.get(node.item, 0) > 0
        return True

    def _route_index(self, x, y):
        column = x if y % 2 == 0 else self.board_size - 1 - x
        return y * self.board_size + column
