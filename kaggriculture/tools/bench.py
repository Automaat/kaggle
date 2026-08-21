"""Seat-swapped, paired Kaggriculture benchmark.

Examples:
  uv run python tools/bench.py main.py starter 60
  uv run python tools/bench.py main.py --pool agents_0.0.x/v10_livestock.py,specialist:melon --seeds 100 --held-out
  uv run python tools/bench.py main.py --pool default --seed-start 20000 --seeds 200
"""

import argparse
import math
import os
import pathlib
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from runner import run_match

DEFAULT_POOL = (
    "champion",
    "agents_1.0.x/v1_10_0_endgame_loads.py",
    "agents_1.0.x/v1_9_0_endgame.py",
    "agents_1.0.x/v1_8_0_loads.py",
    "agents_1.0.x/v1_7_0_lastday.py",
    "agents_1.0.x/v1_6_0_motion.py",
    "agents_1.0.x/v1_5_0_layout.py",
    "agents_1.0.x/v1_4_0_herd.py",
    "agents_1.0.x/v1_3_0_clusters.py",
    "agents_1.0.x/v1_2_0_market.py",
    "agents_1.0.x/v1_1_0_herd.py",
    "agents_1.0.x/v1_0_0_land.py",
    "agents_0.0.x/v0_22_0_supply.py",
    "agents_0.0.x/v20_audit.py",
    "agents_0.0.x/v16_endgame.py",
    "specialist:melon",
    "specialist:strawberry",
    "specialist:dairy",
)
HELD_OUT_START = 100_000


def _one(job):
    candidate, opponent, seed = job
    _, first, first_status = run_match(candidate, opponent, seed=seed)
    _, second, second_status = run_match(opponent, candidate, seed=seed)
    return {
        "seed": seed,
        "candidate": (first[0], second[1]),
        "opponent": (first[1], second[0]),
        "statuses": (first_status[0], first_status[1], second_status[1], second_status[0]),
    }


def _binomial_ci(wins, n):
    """Wilson interval, including uncertainty for unanimous results."""
    if n == 0:
        return 0.0, 0.0
    z = 1.96
    phat = wins / n
    denom = 1 + z * z / n
    centre = (phat + z * z / (2 * n)) / denom
    spread = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - spread), min(1.0, centre + spread)


def summarize(candidate, opponent, results):
    """Normalize both seats and keep the seed as the paired sampling unit."""
    game_diffs = [a - b for result in results
                  for a, b in zip(result["candidate"], result["opponent"])]
    seed_diffs = [statistics.mean(a - b for a, b in zip(result["candidate"], result["opponent"]))
                  for result in results]
    wins = sum(diff > 0 for diff in game_diffs)
    ties = sum(diff == 0 for diff in game_diffs)
    seed_points = [statistics.mean(1.0 if diff > 0 else 0.5 if diff == 0 else 0.0
                                   for diff in (a - b for a, b in zip(result["candidate"], result["opponent"])))
                   for result in results]
    points = statistics.mean(seed_points) if seed_points else 0.0
    points_margin = (1.96 * statistics.stdev(seed_points) / math.sqrt(len(seed_points))
                     if len(seed_points) > 1 else 0.0)
    lo, hi = max(0.0, points - points_margin), min(1.0, points + points_margin)
    margin = (1.96 * statistics.stdev(seed_diffs) / math.sqrt(len(seed_diffs))
              if len(seed_diffs) > 1 else 0.0)
    failures = sum(status != "DONE" for result in results for status in result["statuses"])
    return {
        "candidate": candidate,
        "opponent": opponent,
        "seeds": len(results),
        "seed_start": results[0]["seed"] if results else None,
        "seed_end": results[-1]["seed"] if results else None,
        "games": len(game_diffs),
        "wins": wins,
        "ties": ties,
        "points": points,
        "win_lo": lo,
        "win_hi": hi,
        "mean_diff": statistics.mean(seed_diffs) if seed_diffs else 0.0,
        "margin": margin,
        "mean_score": statistics.mean(a for r in results for a in r["candidate"]),
        "failures": failures,
    }


def _print(summary):
    significant = summary["seeds"] > 1 and abs(summary["mean_diff"]) > summary["margin"]
    print(f'{summary["candidate"]} vs {summary["opponent"]}  '
          f'({summary["seeds"]} paired seeds {summary["seed_start"]}..{summary["seed_end"]}, '
          f'{summary["games"]} games)')
    print(f'  win rate  : {summary["wins"]}/{summary["games"]} '
          f'(points={summary["points"]:.0%}, seed-clustered 95% CI '
          f'{summary["win_lo"]:.0%}-{summary["win_hi"]:.0%}, ties={summary["ties"]})')
    print(f'  seat-pair : {summary["mean_diff"]:>+9.0f}  +/- {summary["margin"]:.0f}   '
          f'{"SIGNIFICANT" if significant else "not significant"}')
    print(f'  candidate : mean={summary["mean_score"]:>9.0f}  failures={summary["failures"]}')


def _arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", nargs="?", default="main.py")
    parser.add_argument("opponent", nargs="?", default=None)
    parser.add_argument("legacy_n", nargs="?", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--pool", help="comma-separated opponents; 'default' uses the regression pool")
    parser.add_argument("--seeds", type=int, default=None, help="number of paired seeds")
    parser.add_argument("--seed-start", type=int, default=0)
    parser.add_argument("--held-out", action="store_true", help=f"start at seed {HELD_OUT_START}")
    parser.add_argument("--workers", type=int, default=os.cpu_count())
    return parser.parse_args()


def main():
    args = _arguments()
    n = args.seeds if args.seeds is not None else (args.legacy_n or 60)
    if n < 1:
        raise SystemExit("seed count must be positive")
    start = HELD_OUT_START if args.held_out else args.seed_start
    if args.pool:
        opponents = (DEFAULT_POOL if args.pool == "default" else
                     tuple(part.strip() for part in args.pool.split(",") if part.strip()))
    else:
        opponents = (args.opponent or "champion",)
    jobs = [(args.candidate, opponent, seed)
            for opponent in opponents for seed in range(start, start + n)]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        raw = list(pool.map(_one, jobs, chunksize=1))

    summaries = []
    offset = 0
    for opponent in opponents:
        result = summarize(args.candidate, opponent, raw[offset:offset + n])
        summaries.append(result)
        _print(result)
        print()
        offset += n
    if len(summaries) > 1:
        print("pool aggregate")
        total_games = sum(row["games"] for row in summaries)
        total_wins = sum(row["wins"] for row in summaries)
        total_ties = sum(row["ties"] for row in summaries)
        points = (total_wins + 0.5 * total_ties) / total_games
        print(f"  win rate  : {total_wins}/{total_games} (points={points:.0%}, ties={total_ties})")
        print(f"  opponents : {sum(row['mean_diff'] > 0 for row in summaries)}/{len(summaries)} positive")


if __name__ == "__main__":
    main()
