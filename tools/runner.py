"""Shared helpers to run Kaggriculture episodes locally."""

import importlib.util
import sys
from pathlib import Path

from kaggle_environments import make

ROOT = Path(__file__).resolve().parent.parent
BUILTIN = {"pass", "random", "starter"}


def load_agent(name):
    """Resolve a builtin agent name, or a python file exposing `agent`."""
    if name in BUILTIN:
        return name
    path = Path(name)
    if not path.is_absolute():
        path = ROOT / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module.agent


def run_match(agent_a, agent_b, seed=None, debug=False):
    config = {"episodeSteps": 720}
    if seed is not None:
        config["seed"] = seed
    env = make("kaggriculture", configuration=config, debug=debug)
    env.run([load_agent(agent_a), load_agent(agent_b)])
    rewards = [s.reward for s in env.steps[-1]]
    statuses = [s.status for s in env.steps[-1]]
    return env, rewards, statuses
