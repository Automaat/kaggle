import copy
import hashlib
import json
from collections.abc import Mapping
from typing import Protocol


ACTION_KEYS = frozenset({"farmer", "hands", "market"})


class OfflineActionProvider(Protocol):
    def reset(self) -> None: ...

    def act(self, observation) -> dict: ...


class ProviderFactory(Protocol):
    def __call__(self) -> OfflineActionProvider: ...


class ProviderExecutionError(RuntimeError):
    def __init__(self, phase, source_step, message):
        self.phase = phase
        self.source_step = source_step
        super().__init__(f"{phase} failed at step {source_step}: {message}")


def source_step(observation):
    if not hasattr(observation, "items"):
        raise TypeError("observation must be a mapping")
    if "step" in observation:
        value = observation["step"]
    else:
        day = observation.get("day")
        hour = observation.get("hour")
        if type(day) is not int or type(hour) is not int:
            raise TypeError("observation day and hour must be integers")
        value = day * 24 + hour
    if type(value) is not int:
        raise TypeError("observation step must be an integer")
    if value < 0:
        raise ValueError("observation step must be nonnegative")
    return value


def observation_identity(observation):
    values = dict(observation.items())
    values.pop("remainingOverageTime", None)
    encoded = json.dumps(
        values,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def validate_action(action):
    if not isinstance(action, Mapping):
        raise TypeError("provider action must be a mapping")
    if set(action) != ACTION_KEYS:
        raise ValueError("provider action has wrong keys")
    result = copy.deepcopy(dict(action))
    if any(type(result[key]) is not list for key in ACTION_KEYS):
        raise TypeError("provider action values must be lists")
    if not result["farmer"]:
        raise ValueError("farmer action must be nonempty")
    if any(type(item) is not list or not item for item in result["hands"]):
        raise ValueError("hand actions must be nonempty lists")
    if any(type(item) is not list or not item for item in result["market"]):
        raise ValueError("market orders must be nonempty lists")
    return result


class CallableActionProvider:
    def __init__(self, action):
        if not callable(action):
            raise TypeError("action provider must be callable")
        self._action = action

    def reset(self):
        return None

    def act(self, observation):
        return self._action(observation)


class ProviderAgent:
    def __init__(self, provider):
        if not callable(getattr(provider, "reset", None)):
            raise TypeError("provider reset must be callable")
        if not callable(getattr(provider, "act", None)):
            raise TypeError("provider act must be callable")
        self.provider = provider
        self._last_step = None
        self._last_identity = None
        self._last_action = None

    def _reset(self, step):
        self._last_step = None
        self._last_identity = None
        self._last_action = None
        try:
            self.provider.reset()
        except Exception as error:
            raise ProviderExecutionError("reset", step, str(error)) from error

    def __call__(self, observation, configuration=None):
        step = source_step(observation)
        identity = observation_identity(observation)
        if identity == self._last_identity:
            return copy.deepcopy(self._last_action)
        if self._last_step is None or step <= self._last_step:
            self._reset(step)
        try:
            raw_action = self.provider.act(copy.deepcopy(observation))
        except Exception as error:
            raise ProviderExecutionError("act", step, str(error)) from error
        try:
            action = validate_action(raw_action)
        except Exception as error:
            raise ProviderExecutionError("validate", step, str(error)) from error
        self._last_step = step
        self._last_identity = identity
        self._last_action = copy.deepcopy(action)
        return copy.deepcopy(action)


def make_provider_agent(factory):
    if not callable(factory):
        raise TypeError("provider factory must be callable")
    return ProviderAgent(factory())
