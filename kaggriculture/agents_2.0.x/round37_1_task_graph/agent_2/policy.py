from .baseline import BaselinePolicy
from .coordinator import Agent2Coordinator, FrozenEconomyPlanner
from .model import normalize_observation
from .state import EpisodeState


class Agent2Policy:
    def __init__(self, baseline_path=None, economy_factory=None, strategy_factory=None):
        self.state = EpisodeState()
        self.baseline = BaselinePolicy(baseline_path)
        factory = economy_factory or FrozenEconomyPlanner
        if strategy_factory is None:
            self.coordinator = Agent2Coordinator(self.baseline, factory())
        else:
            self.coordinator = Agent2Coordinator(
                self.baseline,
                factory(),
                strategy_factory(),
            )

    def act(self, obs) -> dict:
        world = normalize_observation(obs)
        event = self.state.observe(world)
        if event.duplicate:
            return self.state.cached_action()
        if event.reset and self.state.episode > 1:
            self.baseline.reset()
            self.coordinator.reset()
        decision = self.coordinator.decide(obs, world)
        self.state.record(decision.action, decision.task_graph)
        return decision.action
