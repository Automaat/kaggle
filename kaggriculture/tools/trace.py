"""Print a per-day trace of one match: uv run python tools/trace.py [a] [b] [seed]"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from runner import run_match

if __name__ == "__main__":
    a = sys.argv[1] if len(sys.argv) > 1 else "main.py"
    b = sys.argv[2] if len(sys.argv) > 2 else "starter"
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 42

    env, rewards, _ = run_match(a, b, seed=seed)
    watch = ["WHEAT", "CARROT", "TOMATO", "STRAWBERRY", "MELON", "EGG", "MILK", "WOOL"]
    print("day  money_a  money_b  " + "  ".join(f"{w[:4]:>5s}" for w in watch) + "   shops")
    for step in env.steps:
        obs = step[0].observation
        if obs["hour"] != 0:
            continue
        prices = obs["market"]["prices"]
        row = "  ".join(f"{prices[w]:>5d}" for w in watch)
        shops = len(obs["town"]["unlocked_shops"])
        print(f"{obs['day']:>3d}  {obs['farms'][0]['money']:>7.0f}  {obs['farms'][1]['money']:>7.0f}  {row}   {shops}")
