import importlib.util
import json
import pathlib
import sys

from kaggle_environments.utils import Struct


ROOT = pathlib.Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "agents_2.0.x/round37_0_shell"


def _load_model():
    previous_path = list(sys.path)
    previous_modules = {
        name: module for name, module in sys.modules.items()
        if name == "agent_2" or name.startswith("agent_2.")
    }
    for name in previous_modules:
        sys.modules.pop(name, None)
    try:
        sys.path.insert(0, str(PACKAGE))
        specification = importlib.util.find_spec("agent_2.model")
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        return module
    finally:
        for name in list(sys.modules):
            if name == "agent_2" or name.startswith("agent_2."):
                sys.modules.pop(name, None)
        sys.modules.update(previous_modules)
        sys.path[:] = previous_path


def test_normalization_is_lossless_and_immutable():
    model = _load_model()
    observation = {
        "step": 7,
        "player": 1,
        "day": 0,
        "hour": 7,
        "unknown": {"ordered": [1, 1.0, True, None, "x"]},
    }
    before = json.loads(json.dumps(observation))
    world = model.normalize_observation(observation)
    assert model.thaw(world) == observation
    assert observation == before
    assert world.step == 7
    assert world.player == 1


def test_dict_and_struct_normalize_to_same_world():
    model = _load_model()
    observation = {"day": 2, "hour": 3, "player": 0, "nested": {"values": [1, 2]}}
    assert model.normalize_observation(observation) == model.normalize_observation(
        Struct(**observation)
    )


def test_integer_and_float_have_distinct_identity():
    model = _load_model()
    integer = {"step": 0, "day": 0, "hour": 0, "player": 0, "value": 1}
    decimal = {"step": 0, "day": 0, "hour": 0, "player": 0, "value": 1.0}
    assert model.normalize_observation(integer).identity != model.normalize_observation(
        decimal
    ).identity


def test_runtime_budget_does_not_change_observation_identity():
    model = _load_model()
    first = {"step": 0, "day": 0, "hour": 0, "player": 0, "remainingOverageTime": 60.0}
    second = {**first, "remainingOverageTime": 59.0}
    assert model.normalize_observation(first).identity == model.normalize_observation(
        second
    ).identity
    assert model.thaw(model.normalize_observation(first))["remainingOverageTime"] == 60.0
