"""Compare crop mixes head to head: uv run python tools/sweep.py [n_seeds]

Each candidate mix plays main.py against the same opponent mix on the same
seeds, so the only difference between rows is the mix itself.
"""

import os
import pathlib
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

CANDIDATES = [
    "MELON:5,STRAWBERRY:2,CARROT:2",
    "MELON:4,STRAWBERRY:1,CARROT:1",
    "MELON:3,STRAWBERRY:1",
    "MELON:3,STRAWBERRY:1,TOMATO:1",
    "MELON:2,STRAWBERRY:1,CARROT:1",
]
OPPONENT = "MELON:5,STRAWBERRY:2,CARROT:2"


def _one(job):
    mix, seed = job
    os.environ["KAGG_MIX_0"] = mix
    os.environ["KAGG_MIX_1"] = OPPONENT
    from runner import run_match

    _, rewards, _ = run_match("main.py", "main.py", seed=seed)
    return mix, rewards


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    jobs = [(mix, seed) for mix in CANDIDATES for seed in range(n)]
    with ProcessPoolExecutor(max_workers=os.cpu_count()) as pool:
        results = list(pool.map(_one, jobs, chunksize=1))

    by_mix = {}
    for mix, rewards in results:
        by_mix.setdefault(mix, []).append(rewards)

    print(f"opponent: {OPPONENT}   seeds: {n}\n")
    rows = []
    for mix, games in by_mix.items():
        mine = [g[0] for g in games]
        wins = sum(1 for g in games if g[0] > g[1])
        rows.append((wins, statistics.mean(mine), mix, statistics.pstdev(mine)))
    rows.sort(reverse=True)
    print(f"{'mix':<40s} {'wins':>7s} {'mean':>9s} {'sd':>8s}")
    for wins, mean, mix, sd in rows:
        print(f"{mix:<40s} {wins:>4d}/{n:<2d} {mean:>9.0f} {sd:>8.0f}")
