import json

from .domain import World


def normalize_observation(obs) -> World:
    if not hasattr(obs, "items"):
        raise TypeError("observation must be a mapping")
    values = dict(obs.items())
    overage_present = "remainingOverageTime" in values
    overage_value = values.pop("remainingOverageTime", None)
    data = json.dumps(values, allow_nan=False, ensure_ascii=False, separators=(",", ":"))
    step = int(obs.get("step", obs["day"] * 24 + obs["hour"]))
    return World(step, int(obs["player"]), data, data, overage_present, overage_value)


def thaw(world: World) -> dict:
    values = json.loads(world.data)
    if world.overage_present:
        values["remainingOverageTime"] = world.overage_value
    return values
