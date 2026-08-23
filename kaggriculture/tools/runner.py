"""Shared helpers to run Kaggriculture episodes locally."""

import os

from pathlib import Path

from kaggle_environments import make

from artifact import load_artifact

ROOT = Path(__file__).resolve().parent.parent
BUILTIN = {"pass", "random", "starter"}
CHAMPION = "agents_1.0.x/v1_15_0_staged_field"


class ConfiguredAgent:
    def __init__(self, name, values):
        self.values = values
        self.inner = self._apply(lambda: load_agent(name))

    def _apply(self, callback, *args):
        missing = object()
        previous = {key: os.environ.get(key, missing) for key in self.values}
        os.environ.update(self.values)
        try:
            return callback(*args)
        finally:
            for key, value in previous.items():
                if value is missing:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def __call__(self, obs, _configuration=None):
        return self._apply(self.inner, obs)


def configured_agent(spec):
    name, *settings = spec.split(";")
    values = dict(setting.split("=", 1) for setting in settings)
    return ConfiguredAgent(name, values)


def load_agent(name):
    """Resolve a builtin agent name, a callable, or a python file exposing `agent`."""
    if callable(name):
        return name
    if name == "champion":
        name = CHAMPION
    if name in BUILTIN:
        return name
    if name.startswith("specialist:"):
        from specialists import specialist

        return specialist(name.partition(":")[2])
    if name.startswith("current:"):
        from specialists import current_specialist

        return current_specialist(name.partition(":")[2])
    if name.startswith("variant:"):
        from variants import variant

        return variant(name.partition(":")[2])
    if name.startswith("configured:"):
        return configured_agent(name.partition(":")[2])
    path = Path(name)
    if not path.is_absolute():
        path = ROOT / name
    return load_artifact(path)


def run_match(agent_a, agent_b, seed=None, debug=False):
    config = {"episodeSteps": 720}
    if seed is not None:
        config["seed"] = seed
    env = make("kaggriculture", configuration=config, debug=debug)
    env.run([load_agent(agent_a), load_agent(agent_b)])
    rewards = [s.reward for s in env.steps[-1]]
    statuses = [s.status for s in env.steps[-1]]
    return env, rewards, statuses
