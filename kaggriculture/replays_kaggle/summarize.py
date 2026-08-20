"""Turn raw ladder replays into small per-episode records.

    uv run python replays_kaggle/summarize.py
    uv run python replays_kaggle/summarize.py 94619184

Writes `summaries/<episode>.json`, one record per episode, small enough to
commit. The raw replays stay gzipped in `episodes/`: the seed reproduces the
world but not the opponent's policy, and their per-turn actions are the reason
to keep a ladder replay at all.
"""

import argparse
import gzip
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
EPISODES = HERE / "episodes"
SUMMARIES = HERE / "summaries"
PRODUCTS = ("WHEAT", "CARROT", "TOMATO", "STRAWBERRY", "MELON", "EGG", "MILK", "WOOL", "FERTILIZER")
MAX_ORDERS = 10


def _load(path):
    with gzip.open(path, "rt") as handle:
        return json.load(handle)


def _farm_state(farm):
    crops, animals, tiles, weeds = {}, {}, 0, 0
    for row in farm["tiles"]:
        for tile in row:
            if tile == "LOCKED":
                continue
            tiles += 1
            if not isinstance(tile, dict):
                continue
            if "animal" in tile:
                animals[tile["animal"]] = animals.get(tile["animal"], 0) + 1
            elif tile.get("kind") == "PLANT":
                crops[tile["crop"]] = crops.get(tile["crop"], 0) + 1
            elif tile.get("kind") == "WEED":
                weeds += 1
    return {
        "money": farm["money"],
        "tiles": tiles,
        "hands": len(farm.get("hands", [])),
        "crops": crops,
        "animals": animals,
        "weeds": weeds,
    }


def _sales(step):
    sold = [{}, {}]
    for seat, seat_step in enumerate(step):
        action = seat_step.get("action") or {}
        for order in (action.get("market") or [])[:MAX_ORDERS]:
            if isinstance(order, (list, tuple)) and len(order) >= 3 and order[0] == "SELL":
                sold[seat][order[1]] = sold[seat].get(order[1], 0) + order[2]
    return sold


def summarize(replay):
    info = replay.get("info", {})
    days = {}
    for step in replay["steps"]:
        observation = step[0]["observation"]
        day = observation["day"]
        entry = days.setdefault(day, {
            "day": day,
            "players": [None, None],
            "prices": {},
            "inventory": {},
            "shops": [],
            "sold": [{}, {}],
        })
        for seat in (0, 1):
            entry["players"][seat] = _farm_state(observation["farms"][seat])
        entry["prices"] = {p: observation["market"]["prices"].get(p) for p in PRODUCTS}
        entry["inventory"] = {p: observation["market"]["inventory"].get(p) for p in PRODUCTS}
        entry["shops"] = list(observation["town"]["unlocked_shops"])
        for seat, sold in enumerate(_sales(step)):
            for product, count in sold.items():
                entry["sold"][seat][product] = entry["sold"][seat].get(product, 0) + count
    return {
        "episode": info.get("EpisodeId"),
        "teams": info.get("TeamNames"),
        "seed": info.get("seed"),
        "rewards": replay.get("rewards"),
        "days": [days[key] for key in sorted(days)],
    }


def _rollup(records):
    """One line per player-episode: what the engine was and how it finished."""
    rows = []
    for record in records:
        final = record["days"][-1]
        for seat, name in enumerate(record["teams"] or ["?", "?"]):
            sold = {}
            for day in record["days"]:
                for product, count in day["sold"][seat].items():
                    sold[product] = sold.get(product, 0) + count
            rows.append({
                "episode": record["episode"],
                "player": name,
                "money": record["rewards"][seat],
                "won": record["rewards"][seat] == max(record["rewards"]),
                "tiles": final["players"][seat]["tiles"],
                "hands": max(day["players"][seat]["hands"] for day in record["days"]),
                "animals": final["players"][seat]["animals"],
                "weeds": max(day["players"][seat]["weeds"] for day in record["days"]),
                "sold": dict(sorted(sold.items(), key=lambda kv: -kv[1])),
            })
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("episodes", nargs="*", help="episode ids; default every stored replay")
    args = parser.parse_args()

    SUMMARIES.mkdir(exist_ok=True)
    paths = ([EPISODES / f"episode-{e}-replay.json.gz" for e in args.episodes]
             if args.episodes else sorted(EPISODES.glob("episode-*-replay.json.gz")))
    records = []
    for path in paths:
        record = summarize(_load(path))
        (SUMMARIES / f"{record['episode']}.json").write_text(json.dumps(record, sort_keys=True))
        records.append(record)
        print(f"{record['episode']}  {record['teams']}  {record['rewards']}")

    rollup = _rollup(records)
    (SUMMARIES / "rollup.json").write_text(json.dumps(rollup, indent=2, sort_keys=True))
    print(f"\n{len(records)} episodes, {len(rollup)} player-episodes -> summaries/rollup.json")


if __name__ == "__main__":
    main()
