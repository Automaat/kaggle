"""Focused benchmark opponents built from the current agent.

These are evaluation probes, not submission candidates.  Each wrapper applies
its profile only while its agent is running, preventing settings from leaking
to the other player in an in-process match.
"""

import importlib.util
import os
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PROFILES = {
    "melon": {
        "KAGG_PLANNER": "fixed",
        "KAGG_MIX": "MELON:1",
        "KAGG_HERD_SPEC": "",
    },
    "strawberry": {
        "KAGG_PLANNER": "fixed",
        "KAGG_MIX": "STRAWBERRY:1",
        "KAGG_HERD_SPEC": "",
    },
    "dairy": {
        "KAGG_PLANNER": "dynamic",
        "KAGG_HERD_SPEC": "COW:8",
        "KAGG_BAN": "GOOSE,SHEEP",
    },
}

CURRENT_PROFILES = {
    "dairy7": {"KAGG_HERD_EXPERIMENT": "COW:7"},
    "crop_only": {"KAGG_HERD_EXPERIMENT": ""},
    "strawberry_dairy": {
        "KAGG_HERD_EXPERIMENT": "COW:8",
        "KAGG_BAN": "MELON,CARROT,WHEAT",
    },
    "carrot": {
        "KAGG_PLANNER": "fixed",
        "KAGG_MIX": "CARROT:1",
        "KAGG_HERD_EXPERIMENT": "",
    },
    "melon": {
        "KAGG_PLANNER": "fixed",
        "KAGG_MIX": "MELON:1",
        "KAGG_HERD_EXPERIMENT": "",
    },
    "land": {"KAGG_DAIRY_LAND_COWS": "4"},
    "front_runner": {"KAGG_ALWAYS_SELL": "1"},
    "holder": {"KAGG_ALWAYS_HOLD": "1"},
}


@contextmanager
def _environment(values):
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _load(profile):
    module_name = f"kaggriculture_specialist_{profile}"
    with _environment(PROFILES[profile]):
        # Frozen v20 prevents candidate-only experiment flags from leaking into
        # the benchmark opponent through the shared process environment.
        spec = importlib.util.spec_from_file_location(module_name, ROOT / "agents_0.0.x/v20_audit.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    return module


def specialist(profile):
    """Return a current-agent clone constrained to one economic strategy."""
    if profile not in PROFILES:
        choices = ", ".join(sorted(PROFILES))
        raise ValueError(f"unknown specialist {profile!r}; choose: {choices}")
    # Fresh module per episode: agent modules retain price/mix history.
    module = _load(profile)

    def agent(obs):
        with _environment(PROFILES[profile]):
            return module.agent(obs)

    agent.__name__ = f"{profile}_specialist"
    return agent


def current_specialist(profile):
    """Return a current-policy opponent with one deliberate specialization."""
    if profile not in CURRENT_PROFILES:
        choices = ", ".join(sorted(CURRENT_PROFILES))
        raise ValueError(f"unknown current specialist {profile!r}; choose: {choices}")
    values = CURRENT_PROFILES[profile]
    module_name = f"kaggriculture_current_specialist_{profile}"
    with _environment(values):
        spec = importlib.util.spec_from_file_location(module_name, ROOT / "main.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

    def agent(obs):
        with _environment(values):
            return module.agent(obs)

    agent.__name__ = f"current_{profile}_specialist"
    return agent
