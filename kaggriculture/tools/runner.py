"""Shared helpers to run Kaggriculture episodes locally."""

from pathlib import Path

from kaggle_environments import make

from artifact import load_artifact

ROOT = Path(__file__).resolve().parent.parent
BUILTIN = {"pass", "random", "starter"}
CHAMPION = "agents_1.0.x/v1_14_0_central_herd.py"


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
