import argparse
import gzip
import json
from dataclasses import dataclass
from pathlib import Path

from economics.rolling_coordinator import canonical_sha256
from routing.execution_provider import convert_execution


@dataclass(frozen=True, slots=True)
class _EmptyHandoff:
    label: str
    epoch: int
    source_step: int
    economic_fingerprint: str
    space_fingerprint: str
    crop_targets: tuple = ()
    animal_intents: tuple = ()
    space_targets: tuple = ()
    market_orders: tuple = ()


def _empty_handoff():
    return _EmptyHandoff(
        "replay-board-only",
        0,
        0,
        canonical_sha256("execution-replay", "economic"),
        canonical_sha256("execution-replay", "space"),
    )


def _tile_state(tile):
    if tile is None:
        return "empty"
    if type(tile) is str:
        return tile.lower()
    if "animal" in tile:
        return "animal"
    return tile.get("kind", "mapping").lower()


def analyze_replay(path, seat=0):
    replay_path = Path(path)
    with gzip.open(replay_path, "rt", encoding="utf-8") as replay_file:
        replay = json.load(replay_file)
    steps = replay.get("steps")
    if type(steps) is not list or not steps:
        raise ValueError("replay steps must be a nonempty list")
    if type(seat) is not int or seat not in (0, 1):
        raise ValueError("seat must be 0 or 1")
    handoff = _empty_handoff()
    tile_states = set()
    operations = set()
    observations = 0
    actionable = 0
    converted_count = 0
    max_units = 0
    max_tasks = 0
    for frames in steps:
        if type(frames) is not list or len(frames) != 2:
            raise ValueError("replay step must contain two frames")
        frame = frames[seat]
        observation = frame.get("observation")
        if type(observation) is not dict:
            raise TypeError("replay frame lacks an observation")
        observations += 1
        player = observation.get("player")
        farms = observation.get("farms")
        if player not in (0, 1) or type(farms) is not list or len(farms) != 2:
            raise ValueError("replay observation has invalid farms")
        farm = farms[player]
        max_units = max(max_units, 1 + len(farm.get("hands", [])))
        tile_states.update(
            _tile_state(tile)
            for row in farm.get("tiles", [])
            for tile in row
        )
        if frame.get("status") == "DONE":
            continue
        actionable += 1
        converted = convert_execution(observation, handoff)
        converted_count += 1
        max_tasks = max(max_tasks, len(converted.tasks))
        operations.update(task.action[0] for task in converted.tasks)
    return {
        "actionable_observations": actionable,
        "converted_observations": converted_count,
        "max_tasks": max_tasks,
        "max_units": max_units,
        "observations": observations,
        "operations": sorted(operations),
        "replay_id": replay.get("id"),
        "seat": seat,
        "tile_states": sorted(tile_states),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("replay")
    parser.add_argument("--seat", type=int, default=0)
    arguments = parser.parse_args()
    print(json.dumps(analyze_replay(arguments.replay, arguments.seat), sort_keys=True))


if __name__ == "__main__":
    main()
