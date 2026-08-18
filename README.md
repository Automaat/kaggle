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
| `agents/` | Frozen past versions, kept as benchmark opponents |
| `tests/` | Pins the local price model to the environment |
| `EXPERIMENTS.md` | Every idea tried and how it scored |

## Current agent (v2)

Crop mix `MELON:4,STRAWBERRY:1,CARROT:1`, tiled deterministically over the farm.
Hires 8 farm hands each morning and assigns every unit to the nearest tile that
needs work (water > harvest > dig > plant).

Selling is trend-aware: the agent re-implements the market price curve, tracks
each product's price once per day, and holds stock while the trend is positive,
selling only the overflow past 70 of the 100 shed slots and always the cheapest
units first. When the trend turns negative it dumps. On the final day it stops
farming at hour 17 and walks every unit to the shed, because produce still in
hand when the season ends is worth nothing.

Beats `starter` 20/20 seeds (mean $36,868 vs $3,560) and the v1 baseline 20/20.

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

1. Buy land (NE $1k, SW $2k, SE $4k). v2 ends with $37k unspent.
2. Animals, especially geese: 1 unit/tile/day forever and eggs barely glut.
3. Fertilizer, for the doubled watering bonus.
4. Opponent modelling: both farms are visible, so the coming glut is predictable.

## Submit

```bash
uv run kaggle competitions submit kaggriculture -f main.py -m "v2 melon mix"
```
