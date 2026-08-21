import copy
from dataclasses import dataclass, field

from .domain import World
from .tasks import TaskGraph


@dataclass(frozen=True, slots=True)
class ObservationEvent:
    duplicate: bool
    reset: bool


@dataclass(slots=True)
class EpisodeState:
    episode: int = 0
    observations: int = 0
    last_world: World | None = None
    last_action: dict | None = field(default=None, repr=False)
    task_graph: TaskGraph | None = None

    def observe(self, world: World) -> ObservationEvent:
        if self.last_world is not None and world.identity == self.last_world.identity:
            return ObservationEvent(True, False)
        reset = self.last_world is None or world.step <= self.last_world.step
        if reset:
            self.episode += 1
            self.observations = 0
            self.last_action = None
            self.task_graph = None
        self.last_world = world
        self.observations += 1
        return ObservationEvent(False, reset)

    def record(self, action: dict, task_graph: TaskGraph | None = None) -> None:
        self.last_action = copy.deepcopy(action)
        self.task_graph = task_graph

    def cached_action(self) -> dict:
        if self.last_action is None:
            raise RuntimeError("duplicate observation has no cached action")
        return copy.deepcopy(self.last_action)
