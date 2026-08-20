"""Run one match and dump a replay: uv run python tools/play.py [a] [b] [seed]"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import json
import sys
from pathlib import Path

from runner import ROOT, run_match

if __name__ == "__main__":
    a = sys.argv[1] if len(sys.argv) > 1 else "main.py"
    b = sys.argv[2] if len(sys.argv) > 2 else "starter"
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 42

    env, rewards, statuses = run_match(a, b, seed=seed, debug=True)
    for name, reward, status in zip((a, b), rewards, statuses):
        print(f"{name:24s} money={reward:>10.0f}  status={status}")

    out = Path(ROOT) / "replays" / f"{Path(a).stem}_vs_{Path(b).stem}_{seed}.json"
    out.write_text(json.dumps(env.toJSON()))
    print(f"replay -> {out}")
