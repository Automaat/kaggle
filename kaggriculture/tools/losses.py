"""Where the harvest goes missing: uv run python tools/losses.py [agent] [opponent] [seed]

Weeds are a symptom and a poor one, because a tile that dies empty costs
nothing. This counts the units instead: yield that died with a plant, yield
still standing when the season ends, produce discarded when the shed overflows,
and animals that starved and left.
"""

import collections
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from runner import load_agent, run_match

SHED_CAPACITY = 100


def _tiles(farm):
    for y, row in enumerate(farm["tiles"]):
        for x, tile in enumerate(row):
            if tile != "LOCKED":
                yield (x, y), tile


def _probe(spec, report):
    inner = load_agent(spec)
    previous = {}
    animals = {}

    def agent(obs):
        farm = obs["farms"][obs["player"]]
        for position, tile in _tiles(farm):
            was = previous.get(position)
            now = tile if isinstance(tile, dict) else None
            if was and was.get("kind") == "PLANT" and now is not None and now.get("kind") == "WEED":
                # A tile going bare is a harvest; only a weed is a death.
                report["died_units"] += was.get("yield_units", 0)
                report["died_tiles"] += 1
            if was and "animal" in was and (now is None or "animal" not in now):
                report["escaped"] += 1
                report["escaped_units"] += was.get("yield_units", 0)
            previous[position] = now
            if isinstance(tile, dict) and "animal" in tile:
                animals[position] = tile
        if obs["hour"] == 23:
            shed = sum(obs["private"].get("shed", {}).values())
            carried = sum(v for inv in obs["private"].get("inventories", []) for v in inv.values())
            report["discarded"] += max(0, shed + carried - SHED_CAPACITY)
        report["standing"] = sum(
            tile.get("yield_units", 0) for _p, tile in _tiles(farm) if isinstance(tile, dict)
        )
        return inner(obs)

    return agent


def main():
    spec = sys.argv[1] if len(sys.argv) > 1 else "main.py"
    opponent = sys.argv[2] if len(sys.argv) > 2 else "champion"
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 42

    report = collections.Counter()
    _, rewards, _ = run_match(_probe(spec, report), opponent, seed=seed)
    print(f"{spec} vs {opponent}, seed {seed}: {rewards[0]:,.0f} vs {rewards[1]:,.0f}")
    print(f"  units lost with dead plants : {report['died_units']:>5d}  over {report['died_tiles']} tiles")
    print(f"  units left standing at close: {report['standing']:>5d}")
    print(f"  units discarded by the shed : {report['discarded']:>5d}")
    print(f"  animals starved and left    : {report['escaped']:>5d}  carrying {report['escaped_units']} units")


if __name__ == "__main__":
    main()
