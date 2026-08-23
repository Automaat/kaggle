"""Revenue by product for one match: uv run python tools/revenue.py [a] [b] [seed]."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from runner import run_match

if __name__ == "__main__":
    a = sys.argv[1] if len(sys.argv) > 1 else "main.py"
    b = sys.argv[2] if len(sys.argv) > 2 else "main.py"
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 3

    env, rewards, _ = run_match(a, b, seed=seed)
    sold, revenue = [{}, {}], [{}, {}]
    for step in env.steps:
        obs = step[0].observation
        prices = obs["market"]["prices"]
        for player, state in enumerate(step):
            action = state.action or {}
            for order in action.get("market", []):
                if order and order[0] == "SELL":
                    item, units = order[1], order[2]
                    sold[player][item] = sold[player].get(item, 0) + units
                    revenue[player][item] = revenue[player].get(item, 0) + units * prices[item]
    print(f"sales, seed {seed}   final: {rewards}\n")
    print(f"{'product':<12s} {'units_a':>8s} {'revenue_a':>10s} {'units_b':>8s} {'revenue_b':>10s}")
    items = set(revenue[0]) | set(revenue[1])
    for item in sorted(items, key=lambda value: -sum(row.get(value, 0) for row in revenue)):
        print(f"{item:<12s} {sold[0].get(item, 0):>8.0f} {revenue[0].get(item, 0):>10.0f} "
              f"{sold[1].get(item, 0):>8.0f} {revenue[1].get(item, 0):>10.0f}")
