"""Read one ladder episode day by day.

    uv run python replays_kaggle/view.py 94619184
    uv run python replays_kaggle/view.py 94619184 --products MELON,EGG,WHEAT
    uv run python replays_kaggle/view.py --ladder

Reads `summaries/<episode>.json`, so run `summarize.py` first.
"""

import argparse
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
SUMMARIES = HERE / "summaries"
DEFAULT_PRODUCTS = "MELON,STRAWBERRY,MILK,EGG,WHEAT"


def _engine(state):
    parts = [f"{crop[:3].lower()}{count}" for crop, count in sorted(state["crops"].items(), key=lambda kv: -kv[1])]
    parts += [f"{animal[:3].lower()}{count}" for animal, count in sorted(state["animals"].items())]
    return " ".join(parts) or "-"


def _show_episode(record, products):
    print(f"episode {record['episode']}  seed {record['seed']}")
    print(f"  {record['teams'][0]} {record['rewards'][0]:.0f}"
          f"   vs   {record['teams'][1]} {record['rewards'][1]:.0f}\n")
    header = f"{'day':>3s} {'money A':>9s} {'money B':>9s} {'tilesA':>6s} {'tilesB':>6s} {'wdA':>3s} {'wdB':>3s}"
    header += "".join(f"{p[:5]:>6s}" for p in products)
    print(header)
    for day in record["days"]:
        a, b = day["players"]
        row = (f"{day['day']:>3d} {a['money']:>9.0f} {b['money']:>9.0f} "
               f"{a['tiles']:>6d} {b['tiles']:>6d} {a['weeds']:>3d} {b['weeds']:>3d}")
        row += "".join(f"{(day['prices'].get(p) or 0):>6.0f}" for p in products)
        print(row)
    last = record["days"][-1]
    print(f"\n  engine A: {_engine(last['players'][0])}")
    print(f"  engine B: {_engine(last['players'][1])}")


def _show_ladder():
    rollup = json.loads((SUMMARIES / "rollup.json").read_text())
    rows = sorted(rollup, key=lambda r: -r["money"])
    print(f"{'player':<24s} {'money':>8s} {'tiles':>6s} {'hands':>6s} {'weeds':>6s}  top sales")
    for row in rows:
        sold = ", ".join(f"{product.lower()} {count}" for product, count in list(row["sold"].items())[:4])
        mark = "*" if row["won"] else " "
        print(f"{mark}{row['player']:<23s} {row['money']:>8.0f} {row['tiles']:>6d} "
              f"{row['hands']:>6d} {row['weeds']:>6d}  {sold}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("episode", nargs="?")
    parser.add_argument("--products", default=DEFAULT_PRODUCTS)
    parser.add_argument("--ladder", action="store_true", help="one line per player-episode instead")
    args = parser.parse_args()

    if args.ladder or not args.episode:
        _show_ladder()
        return
    record = json.loads((SUMMARIES / f"{args.episode}.json").read_text())
    _show_episode(record, [p.strip().upper() for p in args.products.split(",") if p.strip()])


if __name__ == "__main__":
    main()
