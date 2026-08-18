# Experiment log

Every idea tried, the measurement, and the outcome. Numbers come from
`tools/bench.py` (20 seeds unless stated) and `tools/sweep.py`.

Metric note: **win rate against a fixed opponent** is the score that matters, not
mean money. Both players share one market, so a strong opponent drags both means
down and a weak one lifts both.

## Version history

| Version | Change | vs `starter` (20 seeds) |
| --- | --- | --- |
| v0 `agents/v0_carrot.py` | Carrot monoculture, 8 hands, sell shed every turn | 12/20, mean $4,813 |
| v1 `agents/v1_carrot.py` | Trend-aware selling + final-day cash-out | 20/20, mean $6,537 |
| v2 `agents/v2_melon.py` | Crop mix `MELON:4,STRAWBERRY:1,CARROT:1` | 20/20, mean $36,868 |
| v3 `agents/v3_fixes.py` | Hire batching, critical watering, seed-bill selling, mix `MELON:4,STRAWBERRY:2,CARROT:1` | 20/20, mean $38,151 |
| v4 `agents/v4_animals.py` | Animal support added, off by default | same as v3 |
| v5 `agents/v5_harvest.py` | Harvest at the yield cap, not at `max_yield_day` | — |
| v6 `agents/v6_tiling.py` | Fixed the tile-assignment aliasing bug | — |
| v7 `agents/v7_orders.py` | Sells first in the market order list | — |
| v8 `agents/v8_dynamic.py` | Dynamic crop planner replaces the fixed mix | — |
| v9 `agents/v9_peritem.py` | Per-product hold/dump instead of one global flag | — |
| v10 `agents/v10_livestock.py` | Livestock working: 4 cows, care and fertilizer prioritised | 20/20, mean $48,780 |
| v11 `agents/v11_forecast.py` | Forward supply forecast replaces trailing price drift | — |
| v12 `agents/v12_fertilize.py` | FERTILIZE ongoing crops: strawberry and tomato yield doubles | — |
| v16 `main.py` | Steepness-ordered sells, mixed herd, endgame tidy-ups | 20/20, mean **$53,960** |

v2 beats v1 20/20 ($34,914 vs $5,266). v3 beats v2 20/20 ($26,396 vs $13,810).
v4 with the default mix is v3: 3/20 wins and matching means is what a mirror
match looks like here.

Mirror-match numbers run lower than the `starter` numbers throughout, because
two strong farms glut the same market.

## Ideas tried

### Hire the maximum farm hands every day — kept
Hire cost is `fib(n)` per day, so 8 hands cost $54/day against thousands in
output. Never a close call.

### Sell the shed every turn (v0) — replaced
Simple, but dumps into a falling market and leaves the last day's harvest
unsold.

### Trend-aware selling (v1) — kept
Re-implemented the market price curve in `main.py` (`tests/test_price_model.py`
pins it to the environment). Track each product's price once per day; hold while
the 3-day trend is positive, dump when it turns negative. Sell overflow past 70
of the 100 shed slots, cheapest units first, because the cap counts units, not
value.

Worth +36% vs `starter`. The first version held unconditionally and **lost 7/20
to v0** — two carrot farms glut carrot from $35 to $12, and holding into that
collapse is a straight loss. The trend check is what fixed it.

### Final-day cash-out (v1) — kept
Produce in a unit's hands or in the shed at the final bell is worth nothing, and
the season ends before the end-of-day auto-drop can be sold. From hour 17 on day
29 every unit walks to the shed and drops. Large: a full day of harvest.

### Crop mix (v2) — kept, biggest win so far
Carrot monoculture was the mistake. Per tile-day at base price:

| Crop | Yield/tile/day | Base | $/tile/day | Glut curve above `I0` |
| --- | --- | --- | --- | --- |
| Melon | 0.55 | 250 | **137** | `sq` 3.60 — crashes hard |
| Strawberry | 0.24 | 120 | 29 | `linear` 1.60 |
| Carrot | 0.75 | 35 | 26 | `sqrt` 0.70 — absorbs gluts |
| Wheat | 0.80 | 25 | 20 | `log` 0.20 — barely moves |
| Tomato | 0.33 | 60 | 20 | `sqrt` 0.60 |

Melon is worth 5x carrot per tile-day, and no town shop demands melon, so only
the town center drains it. It still crashes to the $1 floor after roughly 150
units, which is why the mix is not pure melon.

Sweep results (8 seeds, opponent in the header):

```
opponent: MELON:2,CARROT:1,WHEAT:1
MELON:2,STRAWBERRY:1,CARROT:1     8/8   26168
MELON:2,TOMATO:1,CARROT:1         8/8   22648
MELON:1                           8/8   21763
TOMATO:1,STRAWBERRY:1             0/8   15653
WHEAT:1                           0/8    9060
CARROT:1                          0/8    5664
```

```
opponent: MELON:5,STRAWBERRY:2,CARROT:2   (20 seeds)
MELON:4,STRAWBERRY:1,CARROT:1    15/20   19994   <- chosen
MELON:5,STRAWBERRY:2,CARROT:2     5/20   21141   (mirror)
MELON:3,STRAWBERRY:1              4/20   19587
MELON:3,STRAWBERRY:1,TOMATO:1     0/20   16646
MELON:2,STRAWBERRY:1,CARROT:1     0/20   15357
```

`MELON:5,STRAWBERRY:2,CARROT:2` had the higher mean but lost the head-to-head.
Mean was the misleading metric here.

### Hire batching (v3) — kept, and it was a bug
Only 10 market orders clear per turn, and every hire is one order. Issuing them
all at hour 0 truncated the sell and seed orders sharing that list, and silently
capped hiring at 10. Now at most 3 hires per turn over the first 4 hours.

Worth a lot on its own: mean against v2 went $19,604 -> $24,997.

Hands are only cheap in small numbers. The n-th hire of the day costs `fib(n)`,
so 8 hands cost $54/day, 14 cost $986/day and 17 cost $4,180/day. Sweeping
hands-per-tile at 25 tiles puts the optimum at 8:

```
0.34 (8 hands)   16/16   24997
0.24 (6 hands)   16/16   24481
0.44 (11 hands)  16/16   22955
0.54 (14 hands)  16/16   22955
```

### Critical watering priority (v3) — kept
A plant already on one dry day dies tonight, so it now outranks every other
task, ordinary watering included. Before this, 5-9 plants per day were left dry
and the farm slowly filled with weeds.

### Sell to cover the seed bill (v3) — kept
The trend-aware hold was starving the farm of cash: money hit $0 on days 4-13
and empty tiles stayed empty because melon seeds were unaffordable. The sell
quota now always covers the cost of the seeds the empty tiles are planned to
hold.

### Animal support (v4) — built, measured, left off
Full pipeline: buy livestock, build the coop or pasture only once the animal is
in the shed, carry wheat out from the shed (feed only moves in hand), then feed,
harvest, collect fertilizer and care, with feeding promoted to critical once an
animal has missed a day. Wheat held back for the herd is excluded from sale.

Every ratio tested loses to the crop-only mix (`agents/v3_fixes.py` opponent):

```
MELON:4,STRAWBERRY:2,CARROT:1   (crop-only baseline)   20784
MELON:4,STRAWBERRY:2,COW:1                     0/6     17312
MELON:4,STRAWBERRY:2,CARROT:1,GOOSE:1          1/10    15509
MELON:4,STRAWBERRY:2,GOOSE:1                   0/6     14624
MELON:4,GOOSE:2,WHEAT:1                        0/6     12832
MELON:5,STRAWBERRY:2,GOOSE:1                   0/10    10838
GOOSE:1,WHEAT:1                                0/6      8090
```

The arithmetic behind the loss:

- **Capital.** A goose is $300 against a melon seed at $80 that returns six
  melons. Cash is the binding constraint early — money bottoms out near $0
  around day 12 even with no animals — so livestock is bought around day 13 and
  the first egg lands on day 17. That leaves about 12 productive days.
- **Feed.** One wheat per animal per day. Bought at market that is $25 rising to
  $48 as town shops drain wheat, which eats most of the $45 egg. Growing the
  wheat instead spends the tile that made the problem worth solving.
- **Labour.** An animal tile wants up to four actions a day (feed, harvest,
  collect, care) plus wheat-fetching trips, against one for a crop tile. Labour
  is already the constraint that killed land buying.

The code stays in `main.py` behind the mix (`GOOSE`, `COW`, `SHEEP` are valid
tokens) and is inert with the default mix. Worth revisiting only if melon's
economics change.

## Ideas rejected

- **Pure melon.** Loses to any melon/strawberry blend: 300 melons of capacity
  against a floor that arrives at about 150 units.
- **Tomato and strawberry as the core.** 0.33 and 0.24 units/tile/day is too
  little throughput; the tiles sit occupied for 11 and 16 days.
- **Unconditional holding.** See v1 above.
- **Buying land.** Tested at every quadrant count, before and after the v3 fixes,
  and with staple-heavy mixes in case the melon glut was to blame. It loses
  every time:

  ```
  KAGG_LAND   wins vs v2   mean        (16 seeds, after the v3 fixes)
  0           16/16        24997
  1           16/16        18953
  2           15/16        18333
  3            7/16        11857
  ```

  The cause is structural. Every tile needs roughly one action per day whatever
  is on it, so twice the land needs twice the labour — but hire cost is
  Fibonacci, so 8 to 16 hands is about 75x the daily bill ($54 to roughly
  $4,000). The extra tiles cannot carry that. Land buying stays behind
  `KAGG_LAND`, defaulting to 0.
- **Fertilizer on melon.** It only moves melon's cap-reaching age from 10 to 8.
  Melon revenue is capped by the glut curve, not by tile turnover, so faster
  melons sell into a worse price. On wheat and carrot the bonus is +2 and +1
  units, worth far less than the $100 the fertilizer costs.

### Mix re-sweep after the v3 fixes
The best mix moved once the farm stopped starving and drying out:

```
opponent: MELON:4,STRAWBERRY:1,CARROT:1   (16 seeds)
MELON:4,STRAWBERRY:2,CARROT:1    16/16   23113   <- chosen
MELON:5,STRAWBERRY:2,CARROT:2    10/16   20479
MELON:3,STRAWBERRY:1             10/16   19900
MELON:1                           9/16   15391
MELON:6,STRAWBERRY:1,CARROT:1     0/16   16452
```

Re-sweep the mix after any change to labour or cash flow; it is not stable.

## The recurring pattern

Three ideas have now been rejected — land, animals, fertilizer — and all three
failed for the same two reasons. **Labour is the hard constraint** (Fibonacci
hire costs make a bigger farm unaffordable), and **melon dominates return on
capital early**, when cash is scarce. Any future idea should be checked against
both before it is built.

## Not tried yet

- **Wheat arbitrage.** Wheat is one of only two products that can be bought back,
  town shops drain it all season, and its price climbs $25 -> $48. Buying early
  with idle mid-game cash and selling late costs no tiles and no labour. Capped
  by the 100-slot shed, so worth roughly $2k — small, but free.
- **Sell into scarcity spikes.** Carrot, tomato and egg use the `hinge` curve, so
  their prices run away once town demand passes `T`. Watch `unlocked_shops` and
  plant into whatever the town just started eating.
- **Opponent modelling.** Both farms are visible. Counting the opponent's melon
  tiles predicts the coming glut and should shift our sell timing.
- **Care and fertilizer actions for animals**, `CARE` banks +1 per fed day.
- **Parameter tuning with CMA-ES** over the mix weights, shed target, drift
  window and cash floor, against a pool of frozen agents.


---

# Exploration round 2

Two subagents audited the simulator source and generated quantified strategy
ideas. Their reports drove most of what follows. Both are cited where their
finding was the origin.

## Corrections to earlier conclusions

### "Labour is the hard constraint" — WRONG
`tools/opstats.py` counts what every unit-turn is spent on. On 25 tiles with 9
units:

```
unit-turns: 5852
  movement : 3281  56.1%
  idle     : 1665  28.5%
  work     :  906  15.5%
```

Nearly a third of the budget is already idle. 216 unit-turns a day against about
82 of actual demand. The Fibonacci hire cost is real but never binds, because we
never want more than 8 hands.

Two experiments confirm it. Watering only when it pays (see below) freed labour
and money went **down**, because the freed turns had nothing to do. A
task-centric assignment that cut movement from 56% to 48% also **lost**, 7/20.

Land still loses, but the reason was misdiagnosed. The real cause is that the
extra tiles were planted with melon, whose total market depth is fixed.

### "Melon dominates" — only until it floors
A price trace of a mirror match shows melon pinned at **$1 to $30 from day 14
onward**, while strawberry climbs to $239, milk to $264 and wool to $251. No
town shop demands melon, so the town drains only 1 a day; the whole melon pie is
about 188 units for **both** players combined. A fixed melon-heavy mix keeps
planting into a floored market.

## Ideas kept

### Harvest at the yield cap, not at `max_yield_day` (v5)
`WATER` adds yield only inside the bonus window and only up to `max_yield`.
Melon's window opens at age 6 and its cap of 6 is reached at age **10**, but
`max_yield_day` is 12 — so the old rule sat on a finished melon for two days.
Harvesting at the cap turns melon into a 10-day crop.

20/20 against v4, $26,328 vs $13,766.

### Tile-assignment aliasing (v6) — a bug
The mix was tiled by `(y * board_size + x) % len(mix)`. With a 10-wide board any
pattern whose length divides 10 collapses to `x % len(mix)`: every tile in a
column gets the same crop and most of the mix is never planted. Three "different"
mixes in a sweep returned byte-identical results, which is what exposed it.
Tiles are now indexed by position in the tile list.

### Sells first in the market order list (v7) — the largest single win
Both players share one descending price curve **per order index**. A sell at
index 0 clears above a sell the opponent placed at index 3, and truncation at 10
orders drops the tail. The old order was HIRE, BUY_LAND, SELL, BUY_SEED — so on
hours 0-3 every sale sat behind three hires, and overflow dropped sales rather
than hires.

Reordering to sells, seeds, feed, livestock, land, hires: **20/20, $27,271 vs
$15,412**. Cost: nothing. (Subagent finding.)

### Dynamic crop planner (v8)
Each empty tile is assigned the crop with the best profit per tile-day, priced
at the market we will actually sell into: `market_price(crop, projected + yield)`.
Quoting at post-harvest inventory is what stops the farm piling into one crop —
each tile allocated pushes the next tile's quote down that crop's glut curve.
The forecast also subtracts the town's drain over the crop's lifespan, which is
exactly computable from `unlocked_shops`.

Beats the best fixed mix 16/20. The drain forecast must be discounted, because
the opponent is producing into the same market:

```
KAGG_DRAIN_FACTOR   wins vs v7   mean
0.25                16/20        26582   <- chosen
0.5                 16/20        26315
0                   15/20        25168
1.0                 10/20        24645
```

### Per-product hold/dump (v9)
The hold decision was one global flag over the whole shed. Melon starts falling
the moment either farm sells one, so every melon-triggered dump also liquidated
the strawberry and milk that climb all season. Now each product is judged on its
own drift. 16/20 against v8. (Subagent finding.)

### Livestock, reversed (v10) — the v4 rejection was a bug, not economics
Four separate defects were starving the animal pipeline:

1. **`CARE` and `COLLECT_FERTILIZER` were priority 7 and 6**, below `PLANT` and
   `BUILD`. `CARE` banks one unit per fed day and pays out on the next
   production, so on a cow (interval 2) it is a **3x** on milk. It almost never
   fired.
2. **Each tile emitted only its most urgent job per turn.** An animal wants four
   actions a day; lower-priority crop work pulled the unit off the tile between
   them. Tiles now emit every pending job at once.
3. **A deadlock on placement.** `PLACE` was gated on the animal being in the
   shed — but picking it up empties the shed slot, so the animal being carried to
   the pasture could never be placed. Two pastures stood empty all season with
   two cows sitting in the shed. Availability now counts shed plus every unit's
   hands.
4. Fertilizer was never collected, and nothing in the game drains fertilizer —
   the curve is untouched all season.

After the fixes, a cow mix beat the crop-only planner **20/20**. Sweeping the
herd against `agents/v9_peritem.py`:

```
COW   herd=3    0/12   27191        SHEEP herd=3    9/12   29230
COW   herd=4   12/12   32404        SHEEP herd=4    6/12   27226
COW   herd=5    7/12   28432        SHEEP herd=5    0/12   16459
COW   herd=6    3/12   23501        GOOSE herd=3    3/16   18223
```

Four cows, verified over 20 seeds: **19/20 against v9, $32,713 vs $25,063.**
Goose is the animal `CARE` helps least (2x, not 3x) and egg has the flattest
curve — which is exactly what v4 tested in five of six runs.

## Ideas tested and rejected in this round

- **Water only when it pays.** A plant dies only after two dry days running, so
  watering every other day keeps it alive; and for unfertilized ongoing crops
  watering never adds yield at all. Cut work from 906 to 695 actions — and lost
  11/20. Idle time rose to 39.7%. The saved labour had no use, and skipping a
  day means a single missed critical watering kills the plant. Daily watering is
  free insurance.
- **Task-centric assignment** (each task, in priority order, takes the nearest
  able unit). Cut movement from 56.1% to 48.5% and lost 7/20. Same reason.
- **Land, again**, now with the water saving freeing labour: still 0/10 at every
  quadrant count.
- **Livestock inside the dynamic planner.** Letting the value model bid for every
  tile floods the farm with animals it cannot pay for, and a tile reserved for an
  unaffordable animal simply sits empty (mean $9,858 vs $30,552). A fixed
  four-tile reservation works; a bidding model does not.
- **Wheat arbitrage**, previously on the lead list. `BUY_PRODUCT` deposits into
  the shed, so 100 wheat occupies the entire 100-slot shed for 17 days and every
  harvest in that window is discarded at the end-of-day drop. The round trip is
  priced to net zero; only the town's drain between buy and sell pays, roughly
  $1,200. A single lost melon harvest costs more. It is a shed-denial attack on
  ourselves. (Subagent finding.)

## Still open

- **Melon as a race.** No shop demands melon, so whoever sells first takes the
  pie. Both farms holding 112 melons: selling first is worth $26,883, selling
  second $7,822. The current trailing 3-day drift reacts too slowly. A forward
  model — `town_drain − forecast combined supply`, both computable — would dump
  melon on sight.
- **Opponent sales are exactly observable.** For every product except wheat and
  fertilizer, `BUY_PRODUCT` is illegal, so
  `delta_inventory = our_sells + their_sells − town_drain`, and all three other
  terms are known. Their tiles give their future harvests exactly. This is
  inference, not estimation.
- **The carrot hinge as an option.** Carrot's `hinge` curve gives p90 $116 and
  p99 $377 at day 29, and the signal is readable on day 3: if the first shop is a
  pet cafe the expected carrot price nearly doubles. Carrot's 3-day cycle means
  tiles can be converted late. Needs 40 seeds to measure — the payoff is
  tail-heavy.
- **Distance-aware cash-out.** Step 718 (day 29, hour 22) is the last actionable
  turn. A unit 8 steps out leaving at hour 17 arrives at hour 25 and its whole
  load is lost, while units next to the shed idle for five hours. Sweeping
  `KAGG_CASHOUT_HOUR` showed no signal yet, because little ripens on day 29.


---

# Exploration round 3

A third subagent looked for angles not already in this log. Its two best ideas —
fertilizing ongoing crops and ordering sells by curve steepness — matched what
the measurements were pointing at, and one of them is the biggest win of the
round.

## Kept

### Forward supply forecast (v11) — biggest win of the round
The hold/dump decision used a trailing 3-day price drift, which reacts three
days after a price starts falling. Both farms' tiles are public and `CROPS`/
`ANIMALS` are fixed, so the supply still to reach the market is a **hard
ceiling, not an estimate**. Compared against the town's drain — also exact, from
`unlocked_shops` — it says directly whether a product's price still has room to
climb:

```
scarcity(item) = drain(item) * days_left - remaining_supply(item)
```

Hold while scarcity exceeds what we hold; dump otherwise.

**20/20 against v10, $43,567 vs $29,874.** Against `starter` it changes almost
nothing ($48.8k either way) — the gain is entirely in head-to-head play, which
is what the leaderboard measures.

### FERTILIZE the ongoing crops (v12)
`_daily_refresh_plants` doubles an ongoing crop's scheduled yield to 2 when the
tile was fertilized **and** watered that day. One `FERTILIZE` covers three days,
and strawberry produces at ages 10, 12, 14, 16 — so **two applications double a
strawberry from 4 units to 8**, same 16-day occupancy. Tomato likewise, at ages
8, 9, 10, 11.

Fertilizer is free: every animal makes one a day and nothing in the game drains
it. Selling it nets about $90; spending it on a strawberry returns two more
strawberries at roughly $240 each.

18/20 against v11.

**Fertilizing one-time crops loses** (1/20). On melon it only moves the
cap-reaching age from 10 to 8, and melon revenue is capped by depth, not by tile
turnover — so faster melons just sell into a worse price. Fertilizer is worth
more on a strawberry or in the market.

### Sells ordered by curve steepness
`held.sort()` sorted ascending by price, so wheat took order index 0 and melon
landed at index 2-4. Two sells of one item at the same index split the curve
evenly, but index 0 clears the whole top before the opponent's index 3 starts.
Dumps are now ordered by how much they lose to being second in line —
`(price now - price after selling the lot) * lot`. 14/20.

### Mixed herd
`LIVESTOCK` was a single string, so every earlier herd sweep varied one animal.
Milk, wool and egg sit on independent curves, so a mixed herd saturates several
instead of glutting one. `COW:4,SHEEP:3` measured best, but only 13/24 — inside
the noise. Kept because the reasoning is sound and it is not worse.

### Opponent fertilizer in the forecast
`fertilized_until_day` is public on every opponent tile. Without reading it the
forecast under-counts a fertilizing opponent's supply by 2x — and our own agent
now fertilizes, so every frozen version is such an opponent.

## Rejected

- **Exact opponent supply inside the planner.** The scarcity model uses it well,
  but feeding the same forecast into per-tile crop choice made everything look
  glutted and lost 0/20. Counting only the opponent's farm (our own standing crop
  is already handled by the marginal pricing loop) recovered to 10/20 — a tie.
  The 0.25 discount factor stays.
- **Watering only when it pays** and **task-centric assignment** — see round 2.
- **Partial-yield late planting.** Valuing a late tile by what it can actually
  reach before day 29 (a carrot sown on day 27 returns 2 units) lost 5/20.
- **Per-unit departure time for the cash-out.** Step 718 — day 29, hour 22 — is
  the last turn the market clears, and a unit 8 steps out that leaves at the same
  hour as a shed-adjacent one never arrives. Correct in principle, and it scored
  **identically to the digit across 20 seeds**. Day-29 behaviour changes the
  final score by exactly zero, because almost nothing ripens that day. Kept, as
  it costs nothing and matters if the endgame ever fills up.
- **Spreading the final liquidation over the last days.** Swept 1 to 10 days:
  identical win rates, means within 2%.

## What the round showed about method

Effect sizes have collapsed relative to the noise. Standard deviation is now
about $11k on a mean of $45k, so a 20-seed run resolves nothing below roughly
10%. Everything after v12 in this round is inside that band. Future work needs
either much larger seed counts or changes big enough to clear it.

## Still open

- **The Lagrangian whole-season planner.** The problem is a concave separable
  knapsack in tile-days: maximise `sum R_c(Q_c)` subject to
  `sum n_c * L_c <= 25 * 30`, where `R_c` is concave because price falls
  monotonically with inventory. That gives a water-filling solution — bisect on
  the shadow price of a tile-day, invert each curve analytically, then place
  plantings earliest-deadline-first. The current planner only allocates tiles
  that are empty today, so it never reserves early tile-days for strawberry
  (which must be sown by day 13) and never caps a crop's seasonal total.
- **The melon race.** Melon depth is 158 units for both players combined and 13
  melon tiles each saturates it exactly. Units 0-78 are worth $17,952; units
  78-156 are worth $8,522. A fertilized first wave reaches the cap at age 8
  instead of 10, and that two-day lead is worth up to $9,430.
- **Adversarial denial** is mostly dead on the arithmetic. Flooring a product
  needs capacity above its market depth, which is true only for melon. Wheat-feed
  denial needs 825 units against a 100-slot shed; fertilizer denial raises the
  price the opponent sells into.
- **Robustness.** Benchmarks are almost all self-play against frozen versions. A
  fertilized melon rusher (dump ~150 melons on day 8) or a fertilized strawberry
  farm would both be untested opponents worth writing.
