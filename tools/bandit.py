"""Pick the best configuration by sequential halving, with a seed floor.

    uv run python tools/bandit.py --grid KAGG_LAND=0,1,2 --grid KAGG_MAX_HANDS=10,12
    uv run python tools/bandit.py "KAGG_LAND=2;KAGG_MAX_HANDS=12" --floor 40

An arm is a `variants.py` spec; the bare default `main.py` is always in the
field. Arms are ranked on the paired per-seed money difference, scored against
the regression pool rather than the mirror, and the survivor is confirmed on a
fresh seed block against today's default.
"""

import argparse
import itertools
import math
import os
import pathlib
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from bench import DEFAULT_POOL, _one

DEFAULT_ARM = "main.py"


def _arm_name(settings):
    return DEFAULT_ARM if not settings else "variant:" + ";".join(f"{k}={v}" for k, v in settings)


def _grid_arms(grids):
    """Cartesian product of repeated `--grid VAR=a,b,c` into variant specs."""
    axes = []
    for grid in grids:
        var, _, values = grid.partition("=")
        axes.append([(var.strip(), value.strip()) for value in values.split(",")])
    return [_arm_name(combo) for combo in itertools.product(*axes)] if axes else []


def _jobs(arms, opponents, seeds):
    for arm in arms:
        for index, seed in enumerate(seeds):
            yield arm, opponents[index % len(opponents)], seed


def _seed_diffs(results):
    """One paired observation per seed: candidate minus opponent, both seats."""
    return [statistics.mean(a - b for a, b in zip(r["candidate"], r["opponent"])) for r in results]


def _interval(values):
    if len(values) < 2:
        return (statistics.mean(values) if values else 0.0), float("inf")
    return statistics.mean(values), 1.96 * statistics.stdev(values) / math.sqrt(len(values))


def _points(results):
    scores = [1.0 if d > 0 else 0.5 if d == 0 else 0.0
              for r in results for d in (a - b for a, b in zip(r["candidate"], r["opponent"]))]
    return statistics.mean(scores) if scores else 0.0


def _play(arms, opponents, seeds, workers):
    jobs = list(_jobs(arms, opponents, seeds))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        raw = list(pool.map(_one, jobs, chunksize=1))
    by_arm = {}
    for (arm, _opponent, _seed), result in zip(jobs, raw):
        by_arm.setdefault(arm, []).append(result)
    return by_arm


def _round_seeds(cursor, count):
    return list(range(cursor, cursor + count))


def _report(arm, results):
    diffs = _seed_diffs(results)
    mean, margin = _interval(diffs)
    return {"arm": arm, "seeds": len(diffs), "mean": mean, "margin": margin,
            "points": _points(results), "diffs": diffs}


def _print_round(label, rows):
    print(f"\n{label}")
    print(f"  {'arm':<52s} {'seeds':>5s} {'paired diff':>13s} {'points':>7s}")
    for row in rows:
        print(f"  {row['arm']:<52s} {row['seeds']:>5d} "
              f"{row['mean']:>+9.0f} +/-{row['margin']:>6.0f} {row['points']:>6.0%}")


def _confirm(arm, opponents, seeds, workers):
    """Paired head-to-head of the survivor against today's default."""
    both = _play([arm, DEFAULT_ARM], opponents, seeds, workers)
    survivor = _seed_diffs(both[arm])
    default = _seed_diffs(both[DEFAULT_ARM])
    paired = [a - b for a, b in zip(survivor, default)]
    mean, margin = _interval(paired)
    return mean, margin, _points(both[arm]), _points(both[DEFAULT_ARM])


def _survivors(rows):
    return [row["arm"] for row in rows[:max(1, int(len(rows) / 2))]]


def _arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("arms", nargs="*", help="variant specs, e.g. \"KAGG_LAND=2;KAGG_MAX_HANDS=12\"")
    parser.add_argument("--grid", action="append", default=[], help="VAR=a,b,c, repeatable")
    parser.add_argument("--floor", type=int, default=40, help="minimum paired seeds per arm in round one")
    parser.add_argument("--confirm", type=int, default=100, help="paired seeds for the final confirmation")
    parser.add_argument("--max-arms", type=int, default=8)
    parser.add_argument("--seed-start", type=int, default=200_000)
    parser.add_argument("--opponent", default="pool", help="'pool', 'champion', or an agent path")
    parser.add_argument("--workers", type=int, default=os.cpu_count())
    return parser.parse_args()


def main():
    args = _arguments()
    arms = [spec if spec.startswith("variant:") or spec.endswith(".py") else "variant:" + spec
            for spec in args.arms] + _grid_arms(args.grid)
    arms = list(dict.fromkeys(arms + [DEFAULT_ARM]))
    if len(arms) > args.max_arms:
        raise SystemExit(f"{len(arms)} arms over the --max-arms {args.max_arms} limit: pre-screen them")
    opponents = list(DEFAULT_POOL) if args.opponent == "pool" else [args.opponent]

    rounds = max(1, math.ceil(math.log2(len(arms)))) if len(arms) > 1 else 1
    cursor = args.seed_start
    live = arms
    for index in range(rounds):
        if len(live) == 1:
            break
        seeds = _round_seeds(cursor, args.floor)
        cursor += len(seeds)
        played = _play(live, opponents, seeds, args.workers)
        rows = sorted((_report(arm, played[arm]) for arm in live), key=lambda r: -r["mean"])
        _print_round(f"round {index + 1}: {len(live)} arms x {len(seeds)} paired seeds "
                     f"(seeds {seeds[0]}..{seeds[-1]})", rows)
        live = _survivors(rows)
        print(f"  keep: {', '.join(live)}")

    survivor = live[0]
    print(f"\nsurvivor: {survivor}")
    if survivor == DEFAULT_ARM:
        print("the default won its own sweep; nothing to confirm")
        return
    seeds = _round_seeds(cursor, args.confirm)
    mean, margin, survivor_points, default_points = _confirm(survivor, opponents, seeds, args.workers)
    print(f"\nconfirmation on fresh seeds {seeds[0]}..{seeds[-1]} vs {DEFAULT_ARM}")
    print(f"  paired difference : {mean:>+9.0f} +/- {margin:.0f}")
    print(f"  points            : {survivor_points:.0%} vs {default_points:.0%}")
    print(f"  verdict           : {'CONFIRMED' if mean - margin > 0 else 'NOT CONFIRMED, arm dropped'}")


if __name__ == "__main__":
    main()
