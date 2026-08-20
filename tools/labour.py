"""Measure unit-turns per tile-day by crop: uv run python tools/labour.py [agent] [opponent] [seeds]

Every land argument in the log rests on `KAGG_HANDS_PER_TILE = 0.34`, one flat
number for every tile. This attributes each work action to the tile the unit is
standing on and divides by the days that tile was occupied, so the number can
be read per crop instead of assumed.
"""

import collections
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from runner import load_agent, run_match

MOVES = ("NORTH", "SOUTH", "EAST", "WEST")
OVERHEAD = ("PASS", "PICKUP", "DROP")
TURNS_PER_DAY = 24

WORK = collections.Counter()
TILE_DAYS = collections.Counter()
TOTALS = collections.Counter()
_SEEN_DAY = {}


def _occupant(tile):
    if not isinstance(tile, dict):
        return "EMPTY"
    if "animal" in tile:
        return tile["animal"]
    if tile.get("kind") == "PLANT":
        return tile.get("crop", "PLANT")
    return tile.get("kind", "EMPTY")


def _count_tile_days(obs, player):
    day = obs["day"]
    if _SEEN_DAY.get(player) == day:
        return
    _SEEN_DAY[player] = day
    for row in obs["farms"][player]["tiles"]:
        for tile in row:
            if tile != "LOCKED":
                TILE_DAYS[_occupant(tile)] += 1


def counting_agent(obs):
    player = obs["player"]
    farm = obs["farms"][player]
    tiles = farm["tiles"]
    _count_tile_days(obs, player)
    action = _INNER(obs)
    units = [farm["farmer"]] + list(farm.get("hands", []))
    for unit, op in zip(units, [action["farmer"]] + list(action["hands"])):
        name = op[0]
        TOTALS["unit_turns"] += 1
        if name in MOVES:
            TOTALS["movement"] += 1
            continue
        if name in OVERHEAD:
            TOTALS[name.lower()] += 1
            continue
        TOTALS["work"] += 1
        x, y = unit
        WORK[_occupant(tiles[y][x])] += 1
    return action


def main():
    a = sys.argv[1] if len(sys.argv) > 1 else "main.py"
    b = sys.argv[2] if len(sys.argv) > 2 else "champion"
    seeds = int(sys.argv[3]) if len(sys.argv) > 3 else 3

    global _INNER
    _INNER = load_agent(a)
    for seed in range(seeds):
        _SEEN_DAY.clear()
        run_match(counting_agent, b, seed=seed)

    turns = max(1, TOTALS["unit_turns"])
    print(f"{a} vs {b}, {seeds} matches")
    print(f"  unit-turns : {turns}")
    print(f"  movement   : {TOTALS['movement'] / turns:.1%}")
    print(f"  work       : {TOTALS['work'] / turns:.1%}")
    print(f"  idle       : {TOTALS['pass'] / turns:.1%}\n")
    print(f"  {'occupant':<12s} {'work ops':>9s} {'tile-days':>10s} {'ops/tile-day':>13s} {'hands/tile':>11s}")
    for occupant, ops in WORK.most_common():
        tile_days = TILE_DAYS[occupant]
        if not tile_days:
            continue
        per_day = ops / tile_days
        print(f"  {occupant:<12s} {ops:>9d} {tile_days:>10d} {per_day:>13.2f} {per_day / TURNS_PER_DAY:>11.3f}")


if __name__ == "__main__":
    main()
