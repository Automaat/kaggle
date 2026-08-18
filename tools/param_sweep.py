"""Sweep agent parameters set through the environment.

    uv run python tools/param_sweep.py KAGG_LAND 0 1 2 3 -- 12

Every candidate plays main.py against a main.py opponent holding the repo
defaults, on the same seeds.
"""

import os
import pathlib
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))


def _one(job):
    var, value, seed = job
    os.environ[f"{var}_SELF"] = ""
    os.environ[var] = value
    from runner import run_match

    # Player 1 keeps the defaults: the variable only reaches player 0.
    os.environ["KAGG_ONLY_PLAYER"] = "0"
    _, rewards, _ = run_match("main.py", "agents/v2_melon.py", seed=seed)
    return value, rewards


if __name__ == "__main__":
    argv = sys.argv[1:]
    n = 12
    if "--" in argv:
        cut = argv.index("--")
        n = int(argv[cut + 1])
        argv = argv[:cut]
    var, values = argv[0], argv[1:]

    jobs = [(var, v, seed) for v in values for seed in range(n)]
    with ProcessPoolExecutor(max_workers=os.cpu_count()) as pool:
        results = list(pool.map(_one, jobs, chunksize=1))

    by_value = {}
    for value, rewards in results:
        by_value.setdefault(value, []).append(rewards)

    print(f"{var}   opponent: agents/v2_melon.py   seeds: {n}\n")
    rows = []
    for value, games in by_value.items():
        mine = [g[0] for g in games]
        wins = sum(1 for g in games if g[0] > g[1])
        rows.append((wins, statistics.mean(mine), value, statistics.pstdev(mine)))
    rows.sort(reverse=True)
    print(f"{'value':<24s} {'wins':>7s} {'mean':>9s} {'sd':>8s}")
    for wins, mean, value, sd in rows:
        print(f"{value:<24s} {wins:>4d}/{n:<2d} {mean:>9.0f} {sd:>8.0f}")
