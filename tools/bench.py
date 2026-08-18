"""Paired benchmark: uv run python tools/bench.py [a] [b] [n_seeds]

Both agents play the same seeds, so the comparison is paired. Reports the win
rate and a paired confidence interval on the per-seed difference — unpaired
means are useless here, because the two farms share one market and a strong
opponent drags both scores down together.
"""

import math
import os
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


def _binomial_ci(wins, n):
    """Wilson interval, so a 20/20 result does not report zero uncertainty."""
    if n == 0:
        return 0.0, 0.0
    z = 1.96
    phat = wins / n
    denom = 1 + z * z / n
    centre = (phat + z * z / (2 * n)) / denom
    spread = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - spread), min(1.0, centre + spread)


if __name__ == "__main__":
    a = sys.argv[1] if len(sys.argv) > 1 else "main.py"
    b = sys.argv[2] if len(sys.argv) > 2 else "starter"
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 60

    with ProcessPoolExecutor(initializer=_init, initargs=(a, b), max_workers=os.cpu_count()) as pool:
        results = list(pool.map(_one, range(n), chunksize=1))

    mine = [r[0] for r in results]
    diffs = [r[0] - r[1] for r in results]
    wins = sum(1 for d in diffs if d > 0)
    lo, hi = _binomial_ci(wins, n)

    margin = 1.96 * statistics.pstdev(diffs) / math.sqrt(n) if n > 1 else 0
    mean_diff = statistics.mean(diffs)

    print(f"{a} vs {b}  ({n} seeds)")
    print(f"  win rate  : {wins}/{n}  ({wins / n:.0%}, 95% CI {lo:.0%}-{hi:.0%})")
    print(f"  diff      : {mean_diff:>+9.0f}  +/- {margin:.0f}   "
          f"{'SIGNIFICANT' if abs(mean_diff) > margin else 'not significant'}")
    print(f"  mine      : mean={statistics.mean(mine):>9.0f}  sd={statistics.pstdev(mine):>8.0f}")
