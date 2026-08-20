"""Load isolated main.py variants without leaking environment into opponents."""

import importlib.util
import os
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


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


def variant(specification):
    """Build an agent from ``KEY=value;KEY=value`` environment settings."""
    values = {}
    for setting in specification.split(";"):
        key, separator, value = setting.partition("=")
        if not separator or not key.startswith("KAGG_"):
            raise ValueError(f"invalid variant setting: {setting!r}")
        values[key] = value
    module_name = f"kaggriculture_variant_{abs(hash(tuple(sorted(values.items()))))}"
    with _environment(values):
        module_spec = importlib.util.spec_from_file_location(module_name, ROOT / "main.py")
        module = importlib.util.module_from_spec(module_spec)
        sys.modules[module_name] = module
        module_spec.loader.exec_module(module)

    def agent(obs):
        with _environment(values):
            return module.agent(obs)

    agent.__name__ = "variant_" + "_".join(f"{key}_{value}" for key, value in values.items())
    return agent
