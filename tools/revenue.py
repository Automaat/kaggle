"""Revenue by product for one match: uv run python tools/revenue.py [a] [b] [seed]

Reconstructs each player's sales from the market inventory delta, net of the
town's known drain, and prices them at the quote in force at the time.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from runner import run_match

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import main as agent_module

if __name__ == "__main__":
    a = sys.argv[1] if len(sys.argv) > 1 else "main.py"
    b = sys.argv[2] if len(sys.argv) > 2 else "main.py"
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 3

    env, rewards, _ = run_match(a, b, seed=seed)
    sold, revenue, best_price = {}, {}, {}
    prev = None
    for step in env.steps:
        obs = step[0].observation
        inv = obs["market"]["inventory"]
        prices = obs["market"]["prices"]
        if prev is not None:
            drain = agent_module._daily_drain(obs["town"]["unlocked_shops"])
            for item, now in inv.items():
                tick = drain.get(item, 0) / 24.0
                net = now - prev[item] + tick
                if net > 0:
                    sold[item] = sold.get(item, 0) + net
                    revenue[item] = revenue.get(item, 0) + net * prices[item]
                best_price[item] = max(best_price.get(item, 0), prices[item])
        prev = dict(inv)

    print(f"combined sales, seed {seed}   final: {rewards}\n")
    print(f"{'product':<12s} {'units':>8s} {'revenue':>10s} {'avg $':>7s} {'peak $':>7s}")
    for item in sorted(revenue, key=lambda i: -revenue[i]):
        n = sold[item]
        print(f"{item:<12s} {n:>8.0f} {revenue[item]:>10.0f} {revenue[item] / n:>7.0f} {best_price[item]:>7.0f}")
