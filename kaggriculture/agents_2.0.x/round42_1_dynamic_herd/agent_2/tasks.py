from dataclasses import dataclass


LegacyTask = tuple[int, int, int, tuple[str, str | None]]


@dataclass(frozen=True, slots=True)
class TaskId:
    day: int
    x: int
    y: int
    operation: str
    item: str | None
    ordinal: int


@dataclass(frozen=True, slots=True)
class TaskNode:
    identifier: TaskId
    priority: int
    x: int
    y: int
    operation: str
    item: str | None
    source_order: int


@dataclass(frozen=True, slots=True)
class TaskGraph:
    day: int
    nodes: tuple[TaskNode, ...]

    @classmethod
    def from_legacy(cls, day: int, tasks) -> "TaskGraph":
        counts = {}
        nodes = []
        for source_order, raw in enumerate(tasks):
            priority, x, y, operation_data = raw
            operation, item = operation_data
            key = (x, y, operation, item)
            ordinal = counts.get(key, 0)
            counts[key] = ordinal + 1
            identifier = TaskId(day, x, y, operation, item, ordinal)
            nodes.append(
                TaskNode(identifier, priority, x, y, operation, item, source_order)
            )
        return cls(day, tuple(nodes))

    @classmethod
    def empty(cls, day: int) -> "TaskGraph":
        return cls(day, ())

    def find(self, identifier: TaskId) -> TaskNode | None:
        return next(
            (node for node in self.nodes if node.identifier == identifier),
            None,
        )
