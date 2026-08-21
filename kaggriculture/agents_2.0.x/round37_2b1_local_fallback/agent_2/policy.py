from .baseline import BaselinePolicy
from .model import normalize_observation
from .scheduler import LocalFallbackQueueScheduler
from .state import EpisodeState


class Agent2Policy:
    def __init__(self, baseline_path=None):
        self.state = EpisodeState()
        self.baseline = BaselinePolicy(baseline_path)
        self.scheduler = LocalFallbackQueueScheduler()

    def act(self, obs) -> dict:
        world = normalize_observation(obs)
        event = self.state.observe(world)
        if event.duplicate:
            return self.state.cached_action()
        if event.reset and self.state.episode > 1:
            self.scheduler.reset()
            self.baseline.reset()
        decision = self.baseline.decide(obs, self.scheduler)
        self.state.record(decision.action, decision.task_graph)
        return decision.action
