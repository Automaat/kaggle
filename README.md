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
uv run python tools/bench.py main.py starter 20     # 20 seeds, win rate + spread
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
| `agents/` | Frozen past versions, kept as benchmark opponents |
| `tests/` | Pins the local price model to the environment |
| `EXPERIMENTS.md` | Every idea tried and how it scored |

## Current agent (v10)

**Dynamic planner.** Every empty tile is assigned the crop with the best profit
per tile-day, priced at the market we will actually sell into — `market_price`
quoted at post-harvest inventory, so each tile allocated pushes the next tile's
quote down that crop's glut curve. The forecast subtracts the town's drain over
the crop's lifespan, which `unlocked_shops` gives exactly.

**Four cows**, fed, cared for and milked daily, with their fertilizer collected.
`CARE` banks a unit per fed day and pays out on the next production, so it is a
3x on a cow; nothing in the game drains fertilizer, so that curve stays intact
all season.

**Sells lead the market order list.** Both players share one descending price
curve per order index, and truncation drops the tail — so a sale must never sit
behind a hire.

**Hold or dump per product, never globally.** Melon falls the moment either farm
sells one; strawberry and milk climb all season.

Hires 8 farm hands over the first four hours. A plant already on one dry day
outranks every other task, because it dies tonight. On the final day farming
stops at hour 17 and every unit walks to the shed — produce still in hand when
the season ends is worth nothing.

Beats `starter` 20/20 seeds (mean **$48,780** vs $3,590), v9 19/20, v3 20/20.

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

1. Treat melon as a race. No shop demands it, so whoever sells first takes the
   pie: on 112 melons each, selling first is worth $26,883 and second $7,822.
2. Opponent sales are exactly observable, not estimated — `BUY_PRODUCT` is
   illegal for most products, so their sells fall out of the inventory delta.
3. The carrot `hinge` as an option: p99 is $377 a carrot, and the signal is
   readable on day 3.

Land and wheat arbitrage were built or costed and rejected. Labour is **not** the
constraint — a third of unit-turns are already idle. See
[EXPERIMENTS.md](EXPERIMENTS.md).

## Submit

```bash
uv run kaggle competitions submit kaggriculture -f main.py -m "v10 dynamic planner + cows"
```
