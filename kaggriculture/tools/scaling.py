import argparse
import collections
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from runner import load_agent, run_match

MOVES = {"NORTH", "SOUTH", "EAST", "WEST"}
OVERHEAD = {"PASS", "PICKUP", "DROP"}


def _units(farm):
    return [farm["farmer"]] + list(farm.get("hands", []))


def _ops(action):
    return [action["farmer"]] + list(action.get("hands", []))


def _owned_tiles(farm):
    return [tile for row in farm["tiles"] for tile in row if tile != "LOCKED"]


def _productive(tile):
    return isinstance(tile, dict) and (tile.get("kind") == "PLANT" or "animal" in tile)


class ScalingProbe:
    def __init__(self, inner):
        self.inner = inner
        self.counts = collections.Counter()
        self.days = set()
        self.last_work = {}

    def agent(self, obs):
        player = obs["player"]
        farm = obs["farms"][player]
        units = _units(farm)
        inventories = obs["private"].get("inventories", [])
        action = self.inner(obs)
        ops = _ops(action)

        self.counts["calls"] += 1
        self.counts["unit_turns"] += len(ops)
        self.counts["market_orders"] += len(action.get("market", []))
        self.counts["hire_orders"] += sum(
            order and order[0] == "HIRE" for order in action.get("market", [])
        )
        self.counts["carried_wheat_turns"] += sum(
            inventory.get("WHEAT", 0) > 0 for inventory in inventories
        )
        carriers = sum(inventory.get("WHEAT", 0) > 0 for inventory in inventories)
        self.counts["carrier_calls"] += carriers > 0
        self.counts["multi_carrier_calls"] += carriers > 1
        self.counts["carried_wheat_units"] += sum(
            inventory.get("WHEAT", 0) for inventory in inventories
        )
        for unit_index, (position, op) in enumerate(zip(units, ops)):
            name = op[0]
            if name in MOVES:
                self.counts["movement"] += 1
            elif name == "PASS":
                self.counts["idle"] += 1
            elif name in OVERHEAD:
                self.counts["overhead"] += 1
            else:
                self.counts["work"] += 1
                key = player, obs["day"], unit_index
                previous = self.last_work.get(key)
                if previous is not None:
                    distance = abs(previous[0] - position[0]) + abs(previous[1] - position[1])
                    self.counts["work_gaps"] += 1
                    self.counts["work_gap_distance"] += distance
                    self.counts["same_tile_work_gaps"] += distance == 0
                self.last_work[key] = tuple(position)
            self.counts["op:" + name] += 1

        key = (player, obs["day"])
        if key not in self.days:
            self.days.add(key)
            owned = _owned_tiles(farm)
            self.counts["owned_tile_days"] += len(owned)
            self.counts["productive_tile_days"] += sum(_productive(tile) for tile in owned)

        if obs["hour"] == 23:
            self.counts["eod_calls"] += 1
            self.counts["eod_carriers"] += carriers
            self.counts["eod_wheat"] += sum(
                inventory.get("WHEAT", 0) for inventory in inventories
            )
            completed = {
                tuple(position): op[0]
                for position, op in zip(units, ops)
                if op and op[0] in {"WATER", "FEED", "CARE"}
            }
            for y, row in enumerate(farm["tiles"]):
                for x, tile in enumerate(row):
                    if not isinstance(tile, dict):
                        continue
                    operation = completed.get((x, y))
                    if tile.get("kind") == "PLANT":
                        self.counts["plant_days"] += 1
                        if not tile.get("watered_today") and operation != "WATER":
                            self.counts["missed_water_days"] += 1
                    if "animal" in tile:
                        self.counts["animal_days"] += 1
                        if not tile.get("fed_today") and operation != "FEED":
                            self.counts["missed_feed_days"] += 1
                        if not tile.get("cared_today") and operation != "CARE":
                            self.counts["missed_care_days"] += 1
        return action


def _run(spec, opponent, seed, seat):
    probe = ScalingProbe(load_agent(spec))

    def observed(obs):
        return probe.agent(obs)

    if seat == 0:
        _, rewards, statuses = run_match(observed, opponent, seed=seed)
    else:
        _, rewards, statuses = run_match(opponent, observed, seed=seed)
    probe.counts["score"] = rewards[seat]
    probe.counts["opponent_score"] = rewards[1 - seat]
    probe.counts["failures"] = sum(status != "DONE" for status in statuses)
    return probe.counts


def _rate(total, numerator, denominator):
    return total[numerator] / max(1, total[denominator])


def summarize(rows):
    total = sum(rows, collections.Counter())
    paired = []
    for index in range(0, len(rows), 2):
        first, second = rows[index:index + 2]
        paired.append(statistics.mean([
            first["score"] - first["opponent_score"],
            second["score"] - second["opponent_score"],
        ]))
    margin = 0.0
    if len(paired) > 1:
        margin = 1.96 * statistics.stdev(paired) / len(paired) ** 0.5
    return {
        "seeds": len(paired),
        "mean_delta": statistics.mean(paired) if paired else 0.0,
        "margin": margin,
        "daily_unit_turns": 24 * _rate(total, "unit_turns", "calls"),
        "daily_hires": 24 * _rate(total, "hire_orders", "calls"),
        "occupancy": _rate(total, "productive_tile_days", "owned_tile_days"),
        "movement": _rate(total, "movement", "unit_turns"),
        "work": _rate(total, "work", "unit_turns"),
        "same_tile_work": _rate(total, "same_tile_work_gaps", "work_gaps"),
        "work_gap_distance": _rate(total, "work_gap_distance", "work_gaps"),
        "idle": _rate(total, "idle", "unit_turns"),
        "missed_water": _rate(total, "missed_water_days", "plant_days"),
        "missed_feed": _rate(total, "missed_feed_days", "animal_days"),
        "missed_care": _rate(total, "missed_care_days", "animal_days"),
        "carried_wheat_turns": total["carried_wheat_turns"],
        "carrier_calls": _rate(total, "carrier_calls", "calls"),
        "multi_carrier_calls": _rate(total, "multi_carrier_calls", "calls"),
        "wheat_per_carrier": _rate(total, "carried_wheat_units", "carried_wheat_turns"),
        "eod_carriers": _rate(total, "eod_carriers", "eod_calls"),
        "eod_wheat": _rate(total, "eod_wheat", "eod_calls"),
        "failures": total["failures"],
    }


def _arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("agent")
    parser.add_argument("opponent", nargs="?", default="champion")
    parser.add_argument("--seed-start", type=int, default=0)
    parser.add_argument("--seeds", type=int, default=10)
    return parser.parse_args()


def main():
    args = _arguments()
    rows = [
        _run(args.agent, args.opponent, seed, seat)
        for seed in range(args.seed_start, args.seed_start + args.seeds)
        for seat in (0, 1)
    ]
    result = summarize(rows)
    print(f"{args.agent} vs {args.opponent}, {result['seeds']} paired seeds")
    print(f"  paired delta : {result['mean_delta']:+.0f} +/- {result['margin']:.0f}")
    print(f"  unit turns   : {result['daily_unit_turns']:.1f} per day")
    print(f"  hires        : {result['daily_hires']:.1f} per day")
    print(f"  occupancy    : {result['occupancy']:.1%}")
    print(f"  movement     : {result['movement']:.1%}")
    print(f"  work         : {result['work']:.1%}")
    print(f"  same tile    : {result['same_tile_work']:.1%}")
    print(f"  work gap     : {result['work_gap_distance']:.2f} tiles")
    print(f"  idle         : {result['idle']:.1%}")
    print(f"  missed water : {result['missed_water']:.1%}")
    print(f"  missed feed  : {result['missed_feed']:.1%}")
    print(f"  missed care  : {result['missed_care']:.1%}")
    print(f"  wheat turns  : {result['carried_wheat_turns']}")
    print(f"  carrier calls: {result['carrier_calls']:.1%}")
    print(f"  multi carrier: {result['multi_carrier_calls']:.1%}")
    print(f"  wheat/carrier: {result['wheat_per_carrier']:.2f}")
    print(f"  EOD carriers : {result['eod_carriers']:.2f}")
    print(f"  EOD wheat    : {result['eod_wheat']:.2f}")
    print(f"  failures     : {result['failures']}")


if __name__ == "__main__":
    main()
