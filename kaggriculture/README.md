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
uv run python tools/play.py  main.py starter 42
uv run python tools/bench.py main.py
uv run python tools/bench.py main.py --pool default --held-out
uv run python tools/bandit.py --grid KAGG_LAND=1,2 --floor 40
uv run python tools/trace.py main.py starter 42
uv run python tools/labour.py main.py champion
uv run pytest -q tests/
```

Built-in opponents: `pass`, `random`, `starter`, `champion`. Any path to a `.py` file with an `agent(obs)` works. `champion` resolves to `runner.CHAMPION`, the frozen current best — every candidate is judged against it, not against `starter`.

## Layout

| Path                   | Role                                                           |
|:-----------------------|:---------------------------------------------------------------|
| `main.py`              | The submission. Must keep an `agent(obs)` at the root.         |
| `tools/runner.py`      | Match harness shared by the scripts                            |
| `tools/play.py`        | Single match, writes `replays/*.json`                          |
| `tools/bench.py`       | Parallel seeded benchmark, paired and seat-swapped             |
| `tools/bandit.py`      | Picks a configuration by sequential halving, with a seed floor |
| `tools/labour.py`      | Unit-turns per tile-day, by crop and by animal                 |
| `tools/losses.py`      | Units lost: dead plants, unharvested yield, shed overflow      |
| `tools/trace.py`       | Per-day price and money trace                                  |
| `tools/sweep.py`       | Crop-mix comparison                                            |
| `tools/param_sweep.py` | Sweeps any `KAGG_*` knob against a fixed opponent              |
| `tools/opstats.py`     | Where every unit-turn goes: movement, idle, work               |
| `tools/revenue.py`     | Units, revenue and average price per product                   |
| `agents_0.0.x/`        | Frozen 0.x versions, kept as benchmark opponents               |
| `agents_1.0.x/`        | The 1.0.x plan and the versions it produces                    |
| `replays_kaggle/`      | Ladder replays pulled from Kaggle, and how to pull them        |
| `research/`            | Background reading on how this class of game is played         |
| `tests/`               | Pins the local price model to the environment                  |
| `EXPERIMENTS.md`       | Every idea tried and how it scored                             |
| `RULES.md`             | The full rule set, kept in the repo instead of in `.venv`      |

## Versioning

Agents follow semantic versioning. `0.N.0` is the historical line: one minor per tuning round, all of it developed against our own frozen agents and synthetic specialists. `agents_0.0.x/v0_carrot.py` is 0.0.0 through to `agents_0.0.x/v0_22_0_supply.py`, which is 0.22.0 and the first version ever submitted to Kaggle.

A **major** bump means the strategy itself changed, not that it was tuned. 1.0.0 is the first such change: the farm buys a quadrant, staffs it with 12 hands and runs a larger herd, which no amount of tuning had ever made pay. It comes from measuring the labour cost of a tile and from 26 real ladder replays, not from another self-play sweep.

## Current agent (1.3.0)

**A second quadrant of board, staffed.** One extra quadrant is bought, hands are capped at 12, and the herd is 6 cows and 4 sheep. Land had lost every previous test because `KAGG_HANDS_PER_TILE` was 0.34 for every tile: on 50 tiles that asks for 17 hands, which the Fibonacci hire cost makes unaffordable, so the farm bought dirt it could not staff. `tools/labour.py` measured the real figure — 1.2 to 1.5 work ops per tile-day for crops, about 3 for animals, so 0.05 to 0.13 hands per tile. At 0.2 the joint sweep of land, hands and herd clears by +$5,614 +/- $2,208 on fresh paired seeds.

**Trip routing.** A unit prefers work within two tiles over higher-priority work further away; a dying plant still outranks the trip. Movement fell from 65.0% of unit-turns to 56.8% and the change confirmed twice on fresh seeds. Radius 1 and 3 both lose, and so does a sticky-target variant.

**Livestock placement outranks tending.** `PLACE` sat below watering, so units picked animals up every morning, carried them all day and never put them down, while `_animal_orders` counted the carried animals as stock and stopped buying. The herd froze at five of the ten it wanted. Placement first takes it to ten by day 24, worth +$3,408 +/- $2,099. The same switch lost $7,779 in round 6, before there was land to put a herd on.

**A day plan per unit.** Each unit gets a strip of the *working* board — the tiles that carry a plant or an animal — fixed for the whole day, and pays a one-tile penalty for taking work outside it. Splitting acreage instead of work leaves a unit idle in bare ground; recomputing the split every turn makes it thrash, which is how the same idea scored 32% in round 9. Worth +$7,140 +/- $2,085 against the pool and 75% of match points against 1.2.0.

**Opponent-aware selling and a longer unwind.** The hidden-shed estimator is on, so a product the rival is sitting on gets sold before they dump it; liquidation starts six days out instead of four; and fertilized output is no longer charged to the glut curve, a 0.22.0 rule that paid on 25 tiles and costs money on 50. Together +$9,685 +/- $1,843 on fresh paired seeds, and 80% of match points against 1.1.0.

**Everything else is 0.22.0**, unchanged: dynamic planner priced on the glut curve, forward supply forecast, future shops priced before they unlock, fertilizer as an input, carried feed counted as inventory, sells leading the order list steepest-curve first.

Against 1.2.0, 1.3.0 takes 75% of match points and +$4,476 +/- $834 over 200 held-out seeds, and is positive against all nine pool opponents. Against 1.1.0, 1.2.0 took 80% and 82% on two independent blocks. Against the submitted 0.22.0, 1.1.0 already scored 88% and +$11,283 +/- $1,606. On the regression pool 1.2.0 is positive against all eight opponents.

On the ladder, 1.1.0 rated 689.3 against 639.7 for 0.22.0, with 9 wins in 17 episodes. Round 11 reads those games: we beat players who buy a bigger board and leave it half empty, and lose to players who turn 62 tiles into 41 plants where our 50 carry 33.

**What did not work**, all reverted and recorded in rounds 8 and 9: a sell schedule releasing stock at the town's drain rate, a denial term pricing a tile at what our volume takes off the rival's price, alternate-day watering, one quadrant per unit, a higher hiring ceiling, a smaller seed reserve, a cash-crop opening, and geese.

## Judging a candidate

`tools/bench.py` compares **paired** — both agents on the same seeds, seats swapped — and reports confidence intervals on the per-seed difference. The margin shrinks with `sqrt(seeds)`: about $1,400 at 60 seeds, $800 at 200, $550 at 400. A match costs 1.5 s and the bench uses every core, so 200 paired seeds (400 games) run in about 75 s. Run 200 by default and 400 before a version bump; read the interval, not the mean.

Judge candidates on **win rate against a fixed opponent**, not mean money: both players share one market, so the opponent's strength moves both means together.

Read the opponent on the header line of every bench run. A merge once restored a copy of `bench.py` whose default opponent was `starter`, and two runs in one session reported 400-0 and 200-0 before anyone noticed they were beating the tutorial agent.

The champion is the gate, not the whole test. It is a mirror, so a candidate can win the mirror by exploiting the champion's own glut curve and still lose to a different strategy. Confirm every kept change on `--pool default --held-out` before freezing a new version.

`tools/bandit.py` picks between whole configurations rather than one knob at a time, because land, hands and mix only win together. It keeps a floor of 40 paired seeds per arm in the first round, ranks on the paired money difference rather than match points, scores against the pool rather than the mirror, and confirms the survivor on a fresh seed block. A herd of 6 cows and 6 sheep won three halving rounds at 94% and then failed that confirmation, which is what the rule is for.

## Findings from the simulator

- Melon is worth about 5x carrot per tile-day at base price, and no town shop demands melon. It still crashes to the $1 floor past roughly 150 units, so the mix blends in strawberry and carrot.
- Town demand can outrun supply and lift prices all season, but two farms dumping the same crop glut it instead: carrot falls $35 to $12 in a mirror match.
- Hand cost is `fib(n)` per day: 8 hands cost $54, 12 cost $376, 16 cost $2,583. Cheap up to about 13, then a wall. 1.1.0 caps at 12.
- Units respawn at the shed each morning and inventories auto-drop into the shed at end of day, so no explicit `DROP` trip is needed except on the final day.
- A fresh plant starts at `consecutive_unwatered = 1`. Water on the planting day or the seed dies that night.
- Premium goods (strawberry, melon, milk, wool) crash to the $1 floor on a glut. Bundle and time those sales.
- Egg and wheat are the opposite: both climbed all season in a real match while one player ran 25 geese, because the town drains them and neither curve punishes volume.
- That does not make them the field's strategy. Across 26 ladder replays the top players sell melon, milk and strawberry, the same premium goods we sell, and beat us on acreage instead: 50 to 100 tiles against our 25. Banning melon scores 15% match points and -$33,366.
- Labour is 1.2 to 1.5 work ops per tile-day for a crop and about 3 for an animal, measured by `tools/labour.py`, not the 0.34 hands per tile the agent assumed for six rounds.
- Weeds are mostly dead plants, not overgrown ground: 99 of 127 at two bought quadrants. Plants die of thirst at 3.1% per tile-day at home and 8.4% in a bought quadrant.

## Sweeping mixes

`KAGG_MIX_0` / `KAGG_MIX_1` only reach the fixed planner (`KAGG_PLANNER=fixed`). Under the dynamic planner, which is the default, the crop choice is priced per tile and the way to bias it is `KAGG_BAN`.

## Next steps

The plan for the next version is [agents_1.0.x/PLAN.md](agents_1.0.x/PLAN.md), and the replay track it leans on is [replays_kaggle/PLAN.md](replays_kaggle/PLAN.md). See [EXPERIMENTS.md](EXPERIMENTS.md) for the full list. The open leads, in order:

1. Plan a unit's day as a route, not as a sequence of nearest jobs. Round 9 measured the cost of not doing it: plants die of thirst at 3.1% per tile-day at home and 8.4% in a bought quadrant, and that leak is what makes a third quadrant lose.
2. A third quadrant is worth about $50,000 of standing crop if it can be tended. Everything else on this list is smaller.
3. Adapt herd composition to realized shops without stranding bought animals.
4. Submit weekly. The ladder is the only gate that is not our own mirror.

## Submit

```bash
uv run kaggle competitions submit kaggriculture -f main.py -m "1.1.0 land and herd"
```
