# Kaggriculture

Agent for the Kaggle [Kaggriculture](https://www.kaggle.com/competitions/kaggriculture) simulation competition.
Two players farm a 10x10 board for 30 days (720 turns, 24 turns/day). Most coins at the end wins.

## Setup

```bash
mise install
mise run install
```

## Play

```bash
uv run python tools/play.py  main.py starter 42     # one match + replay JSON
uv run python tools/bench.py main.py starter 60     # paired, with confidence intervals
uv run python tools/trace.py main.py starter 42     # per-day money and market prices
uv run python tools/sweep.py 20                     # compare crop mixes head to head
uv run pytest -q tests/
```

Built-in opponents: `pass`, `random`, `starter`. Any path to a `.py` file with an `agent(obs)` works.

## Layout

| Path | Role |
| --- | --- |
| `main.py` | The submission. Must keep an `agent(obs)` at the root. |
| `tools/runner.py` | Match harness shared by the scripts |
| `tools/play.py` | Single match, writes `replays/*.json` |
| `tools/bench.py` | Parallel seeded benchmark |
| `tools/trace.py` | Per-day price and money trace |
| `tools/sweep.py` | Crop-mix comparison |
| `tools/param_sweep.py` | Sweeps any `KAGG_*` knob against a fixed opponent |
| `tools/opstats.py` | Where every unit-turn goes: movement, idle, work |
| `tools/revenue.py` | Units, revenue and average price per product |
| `agents/` | Frozen past versions, kept as benchmark opponents |
| `tests/` | Pins the local price model to the environment |
| `EXPERIMENTS.md` | Every idea tried and how it scored |

## Current agent (v22)

**Dynamic planner.** Every empty tile gets the crop with the best profit per
tile-day, priced at the market we will actually sell into — `market_price`
quoted at post-harvest inventory, so each tile allocated pushes the next tile's
quote down that crop's glut curve, and the town's drain over the crop's lifespan
is subtracted from the forecast.

**Forward supply forecast, not price history.** Both farms' tiles are public and
the crop tables are fixed, so remaining supply is a hard ceiling. Against the
town's drain that gives `scarcity = drain * days_left - supply` per product:
hold while it is positive, dump otherwise. This replaced a trailing 3-day price
drift that reacted three days late.

**Future shops are priced before they unlock.** Shop draws are random, but their
per-product expected demand and unlock schedule are fixed. Long-lived crops now
include that expected drain instead of pretending today's town lasts forever.

**Fertilized output is charged to the glut curve.** Strawberry and tomato return
8 units under the existing fertilizer policy, so every allocation advances the
planner's projected inventory by 8 rather than the old, inconsistent 4.

**Feed already in transit is inventory.** Wheat carried by a unit no longer
looks missing to the market planner, eliminating replacement buys while the
original wheat is walking from the shed to an animal.

**Standing supply includes banked output.** The scarcity forecast counts yield
already waiting on crop and animal tiles and assumes our ongoing crops receive
their scheduled future fertilizer applications.

**Fertilizer as an input.** An ongoing crop yields 2 instead of 1 per scheduled
production when fertilized and watered, and one application covers three days —
so two applications double a strawberry from 4 units to 8. Animals produce
fertilizer free and nothing in the game drains it.

**A herd of 4 cows and 3 sheep**, with feed already carried by units included
in its reserve. Larger all-cow targets won the tuning league but failed fresh
validation, so the mixed herd remains the default.

**Sells lead the market order list, steepest curve first.** Both players share
one descending price curve per order index and truncation drops the tail, so a
sale must never sit behind a hire — and the item that loses most to being second
in line goes first.

Against reconstructed v21, v22 scores 84% match points and +$3,621 +/- $1,019
over 60 fresh paired seeds. Carried-feed accounting alone replicated at
+$4,228 +/- $997; repaired supply accounting scored 76% points and
+$1,072 +/- $830 in its direct ablation.

## Sweeping mixes

`main.py` reads its mix from `KAGG_MIX_0` / `KAGG_MIX_1` (falling back to
`KAGG_MIX`, then the built-in default), so two mixes can play each other:

```bash
uv run python tools/sweep.py 20
```

Judge candidates on **win rate against a fixed opponent**, not mean money: both
players share one market, so the opponent's strength moves both means together.

## Findings from the simulator

- Melon is worth about 5x carrot per tile-day at base price, and no town shop
  demands melon. It still crashes to the $1 floor past roughly 150 units, so the
  mix blends in strawberry and carrot.
- Town demand can outrun supply and lift prices all season, but two farms dumping
  the same crop glut it instead: carrot falls $35 to $12 in a mirror match.
- Farm hands are almost free: cost is `fib(n)` per day, so 8 hands cost $54/day.
  Hire the maximum every day.
- Units respawn at the shed each morning and inventories auto-drop into the shed
  at end of day, so no explicit `DROP` trip is needed except on the final day.
- A fresh plant starts at `consecutive_unwatered = 1`. Water on the planting day
  or the seed dies that night.
- Premium goods (strawberry, melon, milk, wool) crash to the $1 floor on a glut
  (`above_target > 1`). Bundle and time those sales.

## Next steps

See [EXPERIMENTS.md](EXPERIMENTS.md) for the full list. The open leads, in order:

1. Tune against downloaded leaderboard replays, not only synthetic specialists.
2. Adapt herd composition to realized shops without stranding bought animals.
3. Model recurrent opponent replanting in the scarcity forecast.

`tools/bench.py` compares **paired** — both agents on the same seeds — and
reports confidence intervals on the per-seed difference. Differences below about
$1,400 are not resolvable at the default 60 seeds. Read the interval, not the
mean.

Land and wheat arbitrage were built or costed and rejected. Labour is **not** the
constraint — a third of unit-turns are already idle. See
[EXPERIMENTS.md](EXPERIMENTS.md).

## Submit

```bash
uv run kaggle competitions submit kaggriculture -f main.py -m "v20 audit fixes"
```
