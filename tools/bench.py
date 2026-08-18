"""Seeded benchmark: uv run python tools/bench.py [a] [b] [n_seeds]"""

import pathlib
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from runner import run_match


def _one(seed):
    _, rewards, _ = run_match(_A, _B, seed=seed)
    return rewards


def _init(a, b):
    global _A, _B
    _A, _B = a, b


if __name__ == "__main__":
    a = sys.argv[1] if len(sys.argv) > 1 else "main.py"
    b = sys.argv[2] if len(sys.argv) > 2 else "starter"
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 20

    with ProcessPoolExecutor(initializer=_init, initargs=(a, b)) as pool:
        results = list(pool.map(_one, range(n)))

    mine = [r[0] for r in results]
    theirs = [r[1] for r in results]
    wins = sum(1 for m, t in zip(mine, theirs) if m > t)
    print(f"{a} vs {b}  ({n} seeds)")
    print(f"  win rate : {wins}/{n}")
    print(f"  mine     : mean={statistics.mean(mine):>9.0f}  sd={statistics.pstdev(mine):>8.0f}  min={min(mine):>8.0f}  max={max(mine):>8.0f}")
    print(f"  theirs   : mean={statistics.mean(theirs):>9.0f}")
