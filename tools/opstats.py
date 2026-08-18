"""Count what every unit-turn is spent on: uv run python tools/opstats.py [agent] [opponent]"""

import collections
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from runner import load_agent, run_match

COUNTS = collections.Counter()


def counting_agent(obs):
    action = _INNER(obs)
    for op in [action["farmer"]] + list(action["hands"]):
        COUNTS[op[0]] += 1
    for order in action.get("market", []):
        COUNTS["mkt:" + order[0]] += 1
    return action


if __name__ == "__main__":
    a = sys.argv[1] if len(sys.argv) > 1 else "main.py"
    b = sys.argv[2] if len(sys.argv) > 2 else "main.py"
    _INNER = load_agent(a)
    _, rewards, _ = run_match(counting_agent, b, seed=3)

    moves = sum(COUNTS[k] for k in ("NORTH", "SOUTH", "EAST", "WEST"))
    unit_turns = sum(v for k, v in COUNTS.items() if not k.startswith("mkt:"))
    print(f"final money: {rewards[0]:.0f}\nunit-turns: {unit_turns}")
    print(f"  movement : {moves:>6d}  {moves / unit_turns:>6.1%}")
    print(f"  idle     : {COUNTS['PASS']:>6d}  {COUNTS['PASS'] / unit_turns:>6.1%}")
    work = unit_turns - moves - COUNTS["PASS"]
    print(f"  work     : {work:>6d}  {work / unit_turns:>6.1%}")
    for op, n in COUNTS.most_common():
        print(f"    {op:<22s} {n:>6d}")
