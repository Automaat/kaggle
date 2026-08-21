from .baseline import BaselinePolicy
from .model import normalize_observation
from .state import EpisodeState


class Agent2Policy:
    def __init__(self, baseline_path=None):
        self.state = EpisodeState()
        self.baseline = BaselinePolicy(baseline_path)

    def act(self, obs) -> dict:
        world = normalize_observation(obs)
        event = self.state.observe(world)
        if event.duplicate:
            return self.state.cached_action()
        if event.reset and self.state.episode > 1:
            self.baseline.reset()
        action = self.baseline.act(obs)
        self.state.record(action)
        return action
