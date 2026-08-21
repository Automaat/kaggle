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
| v16 `agents/v16_endgame.py` | Steepness-ordered sells, mixed herd, endgame tidy-ups | 20/20, mean $53,960 |
| v20 `main.py` | Ten bugs found by a source audit | 60/60, mean **$66,374** |
| v21 `main.py` | Expected future shops + correct fertilized projection | held-out +$1,676 +/- $931 vs v20 |
| v22 `main.py` | Carried feed + exact standing supply | fresh +$3,621 +/- $1,019 vs v21 |

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


---

# Exploration round 4

## First: fix the measurement

Round 3 ended with effect sizes inside the noise. `tools/bench.py` now runs
paired comparisons — both agents on the same seeds — and reports a Wilson
interval on the win rate plus a confidence interval on the **per-seed
difference**. That is roughly ten times more sensitive than comparing unpaired
means, because the shared market moves both scores together.

The first thing it showed: **v16 was not measurably better than v12**
(+$834 +/- $1,334 over 60 seeds). Three of that round's four changes were noise,
exactly as suspected.

Default seed count is now 60. Anything below about $1,400 of difference is not
resolvable at that size.

## Then: a source audit found ten bugs

A subagent read `main.py` and the simulator line by line looking for defects
rather than ideas. It found real ones. Ranked by what they cost:

### The entire day-29 harvest was thrown away
Units left for the shed at `hour >= 22 - walk`, arrived at hour 22, and dropped
**on step 718 — the last turn the interpreter runs**. The market orders for that
turn were built from the pre-drop shed, so the goods were never sold. And
`_tile_task` deliberately force-harvests every ripe tile once `day >= LAST_DAY`,
so the agent pulled the whole farm into its hands and then binned it.

This also explains the round-3 result that per-unit departure timing "scored
identically across 20 seeds". It was not evidence the endgame is worthless —
every departure time lost the load, so shuffling them changed nothing.

Fixed by leaving one turn earlier. With the last-day reserves fix below:
**+$1,033 +/- $694, significant.**

### The mixed herd never ran
`_empty_structures` and the `PLACE` picker both took the **first** entry of
`ANIMALS` matching the structure kind, which for a pasture is always `COW`. So
with `COW:4,SHEEP:3` the sheep were bought ($1,500), never carried, never
placed, and never sold — `SHEEP` is not in `MARKET_PARAMS`. Meanwhile the
pastures still reported as empty, so more cows were bought to fill them.

Both now pick by the remaining herd deficit. This is why v16's "mixed herd"
measured 13/24: it was inert.

### Livestock was re-bought while in a unit's hands
A pasture counts as empty until the animal is *placed*, `PICKUP` takes the whole
shed stock at once, and `PLACE` puts down one per turn — so for several turns the
agent saw a deficit with an empty shed and bought another wave. About $1,600.
The correct count (shed plus every unit's hands) was already in scope.

### Wheat and carrot were harvested one watering early
`WATER` adds a unit inside the bonus window, and the old rule emitted `HARVEST`
alone the moment `age >= max_yield_day` — before that day's watering. Carrot came
in at 2 units instead of 3, wheat at 3 instead of 4. **A quarter of every wheat
and carrot tile**, and `EXPECTED_YIELD` claimed the full figure, so the planner
also overpriced both. Melon was unaffected: it exits on the yield cap, which is
only reached after watering.

### The planner priced strawberry and tomato at half
`_crop_value` used the unfertilized yield while the agent deliberately fertilizes
every ongoing crop. Strawberry was valued at `(4p - 100)/16` when it actually
returns `(8p - 100)/16`. The supply forecast ten lines away already applied the
2x.

### `_fertilize_pays` was off by one
A production at age `p` is computed during the refresh of day `p-1`, and a
`FERTILIZE` at age `a` sets the flag through `a+2` — so it doubles productions at
ages `a+1..a+3`, not `a..a+2`. The old rule fired at strawberry ages 8, 11, 14
(three applications, one of them wasted on the final production, which it could
not affect). The correct rule fires at 9 and 13 and still doubles all four.

### The supply forecast double-counted spent tiles
Any standing ongoing crop contributed its **full** lifetime yield regardless of
how many productions it had already fired, for both farms. So `_scarcity` saw
more glut than existed and flipped climbing products into the dump bucket too
early — undercutting the v11 change that was the previous round's biggest win.

### Last-day reserves were left unsold
Feed wheat and fertilizer were still held back from the day-29 dump, and
`_feed_orders` kept buying wheat on day 29, turning cash into unsellable stock.

### Dead code and fragility
- The whole price-history subsystem (`_drift`, `_record_prices`, `_history`,
  `DRIFT_WINDOW`) ran every day and **nothing read it** — the forward forecast
  replaced it in v11. Removed. `_animal_value`, `CASHOUT_HOUR` and `SHED_CAP`
  were also dead; `CASHOUT_HOUR` was an env knob wired to nothing, so the
  round-3 sweep of it measured literally nothing.
- `configuration.marketParams` can override the price curves. The agent read
  only its frozen copy, so an override would silently mis-price everything with
  no crash and no signal. Now honoured, with a test.
- `SHED_TILE` was hardcoded to `(4,4)` while `boardSize` is configurable down to
  4. Now derived.
- `_tile_task` had one `None` return in a function whose callers iterate the
  result. The simulator swallows agent exceptions and substitutes a default
  action for the rest of the game, so that would have been a silent total loss.
- `BUY_PRODUCT` and `BUY_ANIMAL` are refused once the shed is full; neither
  order path checked.

### Result
**60/60 against `starter`, mean $66,374** (v16: $53,960). Against v16:
**+$4,991 +/- $1,463, significant**, 80% win rate. `tests/test_tables.py` now
pins every hand-derived table — crop constants, production ages, animal rates,
the shop table, the tick rate and the market-param override — to the simulator.

## Also tried this round

- **Adversarial opponents.** `KAGG_PLANNER_1` / `KAGG_MIX_1` turn the agent into
  a specialist opponent. Against a melon monoculture, a strawberry monoculture
  and a cow-and-strawberry farm it wins 16/16 each. Specialists are weak: a
  strawberry monoculture scores $4,042, because strawberry needs 16 days and
  there is no early cash.
- **The planner counting its own pipeline.** Re-planning every turn without
  knowing what it already has growing looks like an obvious gap. Adding it: 42%
  win rate, not significant. Adding the opponent's too: 0/20. The per-turn
  marginal pricing already handles it.
- **Banning crops** to stop melon over-production: banning melon costs
  -$992 +/- $2,609. Not significant either way.
- **Herd size** at 4, 6, 8 and 10 cows: all inside the noise. Capital, not tiles,
  is the limit.

## Where the money actually goes

`tools/revenue.py` reconstructs sales per product from the market inventory
delta. Combined across both farms in one match:

```
product      units   revenue   avg $  peak $
MILK           672    172510     257     289
STRAWBERRY     431    101935     237     271
FERTILIZER     174     14198      82     100
WHEAT          278     12642      45      51
CARROT         213      9798      46      57
TOMATO         134      9391      70      81
EGG            160      9001      56      63
MELON          197      8303      42     272
WOOL            29      6384     222     229
```

---

# Exploration round 5

Every strategy was implemented behind an independent `KAGG_*` switch and
compared with frozen `agents/v20_audit.py`. The new benchmark swaps seats for
every seed; reported differences use the seed-pair as the sampling unit.

## Isolated results

| Experiment | 40 paired seeds vs v20 | Decision |
| --- | ---: | --- |
| Effective fertilized-yield projection | +$783 +/- $519 | keep |
| Expected future shop demand | +$2,106 +/- $1,088 | keep |
| Partial scarcity liquidation | -$1,736 +/- $852 | reject |
| Integrated sale-order loss | +$12 +/- $7 | disabled; immaterial |
| Fertilized first-wave melon race | +$994 +/- $1,369 | disabled; unresolved |
| Deadline-aware seasonal quotas | +$97 +/- $1,932 | disabled; unresolved |
| Opponent hidden-stock trigger | -$1,013 +/- $697 | reject |
| Four-cow NE dairy expansion, day 10 | -$372 +/- $1,206 | disabled; unresolved |

Ten-unit sale lots lost -$8,009 +/- $1,318. Delaying the herd to day 3 lost
-$11,277 +/- $1,965. A seven-cow herd reached 57% held-out wins but only
+$658 +/- $869, so the existing four-cow/three-sheep herd remains the default.

The melon-race policy correctly buys fertilizer, applies it at age 5, reaches
the cap at age 8 and sells immediately. Against the pure-melon specialist it
still reduced our advantage by roughly $4k because v20 already exploits that
opponent without paying for fertilizer.

## Combined result

Effective-yield projection plus future-shop demand:

```
development  seeds 0..59        67% wins   +2176 +/- 910
held-out     seeds 100000..100059 61% wins  +1676 +/- 931
```

Held-out regression pool, 20 paired seeds per opponent:

```
v10 livestock       40/40
v12 fertilize       40/40
v16 endgame         37/40
melon specialist    40/40
strawberry specialist 40/40
dairy specialist    32/40
aggregate          229/240
```

Only the two replicated planner fixes default on. All other implementations
remain switchable for future tuning: `KAGG_PARTIAL_SCARCITY`,
`KAGG_EXACT_SELL_ORDER`, `KAGG_SELL_LOT`, `KAGG_MELON_RACE`,
`KAGG_SEASONAL_PLANNER`, `KAGG_HERD_EXPERIMENT`, `KAGG_HERD_START_DAY`,
`KAGG_HERD_BUY_PER_DAY`, `KAGG_OPPONENT_STOCK`, and
`KAGG_DAIRY_LAND_COWS`.

---

# Exploration round 6

The benchmark now reports ties as half-points and clusters uncertainty by seed;
the two seat-swapped games are not treated as independent samples. Variants are
loaded in isolated modules, preventing `KAGG_*` settings from leaking into the
opponent. All tuning used new seed ranges starting at 200,000.

## Diagnostics

The working livestock pipeline changed the workload materially:

```
movement  65.0%   (was 56.1%)
idle      13.9%   (was 28.5%)
work      21.1%
PICKUP      316
FEED        104
```

Two bugs explained most of the wasted motion and cash:

- Wheat picked up from the shed disappeared from the feed-purchase calculation,
  so it was bought again while still in a unit's hands.
- Scarcity omitted output already held on public tiles and future fertilizer
  applications the agent itself would make.

## Initial screen

| Experiment | 12 paired seeds vs v21 | Decision |
| --- | ---: | --- |
| Count carried feed | +$3,483 +/- $2,559 | validate |
| Exact standing supply | +$1,795 +/- $1,360 | validate |
| Earlier fertilizer collection | +$682 +/- $1,043 | reject |
| Future-shop factor 0.75 | +$814 +/- $1,473 | reject |
| Drain factor 0.20 | +$430 +/- $935 | reject |
| Integrated crop revenue | -$25 +/- $304 | reject |
| Near-shed herd | -$11,808 +/- $3,796 | reject |
| Ten hands | -$7,834 +/- $3,070 | reject |
| Animal harvest batching | -$5,187 +/- $2,326 | reject |
| Duplicate-pickup budget | -$3,391 +/- $2,720 | reject |

Carried feed replicated at +$3,490 +/- $1,653 on a second range and
+$4,228 +/- $997 over 60 untouched seeds. Adding standing-supply accounting
reached +$4,588 +/- $1,040 before a one-time-crop double-count was removed.
After wiring the player identity into live scarcity calls, the corrected supply
increment scored 76% points (+$1,072 +/- $830).

## Herd tournament

The feed fix invalidated every earlier herd sweep. Seven cows beat the old
4-cow/3-sheep target at 71% points. Larger head-to-head results:

```
14 cows vs 10 cows   82% points   +1549 +/- 468
14 cows vs 16 cows   97% points   +7391 +/- 1147
```

Fourteen cow slots then scored 162/168 games (96% points) across crop-only,
strawberry-dairy, carrot, melon, land, front-runner and holder specialists.
Only about seven animals actually reach pastures; promoting `PLACE` completed
more of the herd but lost -$7,779 +/- $3,647. Purchase caps from 5 to 13 did not
improve match points. A final fresh range reversed the result: 14 cow slots
scored only 38% points against v21 while the accounting fixes with the original
mixed herd scored 84%. The 14-slot policy was rejected as league overfitting.

## Post-herd retuning

With 14 slots, crop harvest batching initially gained +$1,636 +/- $1,209 but
failed the 60-seed validation (42% points, +$361 +/- $522). Nine or ten hands,
near-shed placement, fertilizer priority, animal batching, feed reserves of one
or three days, and cash reserves from $200 to $800 all lost. Shed targets from
50 through 90 were behaviorally identical.

Final corrected v22 versus reconstructed v21 on seeds 2,200,000..2,200,059:

```
84% points   +3621 +/- 1019   significant score difference
```

Milk and strawberry are the business. Melon sells 197 units at an average of $42
against a peak of $272 — both farms dump it at the floor. Wool at 29 units was
the inert-sheep bug.

# Round 7: the first real opponent

> Reconstructed on 2026-08-20. An accidental `git reset --hard` during a signing
> check discarded the uncommitted copy of this log. Rounds 7, 7c and 7d are
> restored verbatim from the session that wrote them; round 7b's per-variant
> tables could not be recovered and are summarised instead.

Everything before this round was self-play against our own frozen versions and hand-written specialists. On 2026-08-19 `main.py` (v22) was submitted to Kaggle for the first time. Submission 55630506 validated and played one public episode, 94619184, and **lost**: 47,160 against 49,912 for "Howey do it".

## "Buying land loses" — WRONG, and the refutation is measured

The rejection at the top of this log rested on one structural claim: every tile needs about one action per day whatever is on it, so four quadrants need four times the hands, and the Fibonacci hire cost makes 16 hands cost roughly $4,000 a day.

The opponent ran 100 tiles on **12 hands**, which costs $376 a day.

```
hands   cost/day
    8         54
   10        143
   12        376
   16       2583
   20       6154
```

Our own model asks for `0.34` hands per tile (`KAGG_HANDS_PER_TILE`). The opponent needed `0.12`. The difference is what sits on the tiles. Wheat is planted, watered, and harvested inside four days and needs nothing else. Geese are fed and harvested in one pass down a row. Strawberry and melon demand daily attention for ten to sixteen days.

So the labour cost of land is not a property of land. It is a property of the crop mix put on it, and every land experiment in this log planted the extra quadrants with our existing high-touch mix.

## The compounding test error

Land was always tested alone, and hands were always tested alone.

- `KAGG_LAND` from 1 to 3 lost at every count, with `MAX_HANDS` capped at 10.
- Round 6 tested nine and ten hands on 25 tiles and lost -$7,834, because idle hands have nothing to do.

Neither test could have found the winning setting. `want_hands = min(MAX_HANDS, round(tiles * HANDS_PER_TILE))` means buying land without raising `MAX_HANDS` leaves 100 tiles to 10 hands, and raising hands without buying land leaves them idle. **Land, hands and mix have to move together or all three read as losses.**

## Why wheat and eggs beat strawberry and milk at scale

Final prices in the lost episode:

| Product | Day 0 | Day 29 |
| --- | ---: | ---: |
| Egg | 50 | 62 |
| Wheat | 27 | 54 |
| Strawberry | 128 | 279 |
| Melon | 256 | 13 |
| Milk | 169 | 139 |

The opponent produced eggs from 25 geese for half the season and the egg price still **rose**. Egg uses `log` above `I0` at `above_target 0.20`, so it cannot be glutted; bakeries and brunch spots drain it, and there is no floor risk. Wheat doubled because both players buy it as feed while the town drains it.

Our business is strawberry and milk, both `above_target 1.60`. They pay more per unit but the curve punishes volume, which is exactly why extra tiles never paid: we can only scale into products that collapse when scaled.

Melon fell from 256 to 13 by day 15. We planted 18 melons in the opening; that crash is ours.

Round 9 revisits this reading against 26 replays rather than one, and it does not survive: the top of the ladder sells the same premium goods we do.

## What the loss actually says

We lost by $2,752 with 25 tiles against 100. Per tile our engine is roughly four times better. The ceiling is the problem, not the engine.

## Open

1. Re-cost land with wheat and geese as the target product, not the current mix. Land, `MAX_HANDS` and mix must be swept jointly; sweeping any one alone reproduces the old wrong answer.
2. Measure actions per tile per day by crop, instead of assuming a flat 0.34.
3. Pull more leaderboard replays and check whether full-land wheat-and-egg is the field's dominant strategy or one player's idea.

## Round 7b: land fails on a labour budget the planner does not have

The round 7 replay said land works. A joint sweep of land and hands against frozen 0.22.0 said it does not, on 12 paired seeds each. Every quadrant count lost, and the diagnostic showed why: 41 of the newly unlocked tiles stood as weeds while `_land_profit` priced them as tended. The per-variant tables were lost with the uncommitted log; rounds 7c and 7d below repeat the measurement with the same conclusion.

## Round 7c: a labour budget, and why land still cannot be tuned into paying

Added `KAGG_LABOUR_BUDGET`. It estimates unit-turns per tile-day per crop, from actual required actions over the crop's occupancy, computes a daily capacity from the hiring ceiling, and stops the planner allocating tiles once projected tending demand passes it. When the budget is on, crops are ranked by value per unit of labour rather than value per tile-day.

12 paired seeds against frozen 0.22.0:

| Variant | Points | Seat-pair | Mean |
| :--- | ---: | ---: | ---: |
| budget, one quadrant | 67% | +576 +/- 1,282 | 55,545 |
| budget, `LAND=1` | 0% | -16,143 +/- 3,424 | 43,852 |
| budget, `LAND=2` | 0% | -20,344 +/- 1,979 | 37,308 |
| budget, `LAND=3` | 0% | -22,872 +/- 2,089 | 33,110 |

The budget on its own is mildly positive and unresolved: 67% points but +$576 +/- $1,282. It does not rescue land at any quadrant count or any capacity setting.

### The budget was not the binding constraint

A diagnostic match with the budget on and three quadrants bought: on day 11 the planner holds 50 unlocked tiles and plants 25 of them, leaving 43 empty. It is already restraining itself. Weeds still climb to 42 by day 20.

The real damage is on the other side of the balance sheet:

```
day 14   land  3 cows, 4 empty pastures, $3,828
         base  4 cows, 2 sheep,          $11,199
day 20   land                            $2,024
         base                            $12,033
```

The herd never completes. $7,000 of quadrants is bought with the cash that would otherwise have bought four cows and three sheep for $3,100, and milk is where the money is. We buy dirt instead of the business, and then cannot afford to work either.

Round 9 found a second reason the herd never completes, and it was a bug rather than a budget.

### Copying the opponent's engine directly also fails

Banning the premium crops and running geese, which is what episode 94619184's winner did:

| Variant | Points | Seat-pair | Mean |
| :--- | ---: | ---: | ---: |
| wheat and geese, one quadrant | 0% | -48,163 +/- 3,361 | 22,744 |
| wheat and geese, `LAND=2` | 0% | -56,471 +/- 2,730 | 14,761 |
| wheat and geese, `LAND=3`, 16 geese | 0% | -59,451 +/- 2,755 | 9,780 |

The engine loses by $48,000 **before any land is bought**. This does not refute the strategy. It says our agent cannot execute it: the goose pipeline needs wheat carried from the shed to 25 animals every day, and our logistics were built for seven animals next to the shed.

### Conclusion

Land cannot be made to pay by tuning. Every configuration tested loses, and the two candidate causes are both structural:

1. **Cash sequencing.** Land competes with the herd for the same early money, and the herd wins by a wide margin.
2. **Animal logistics.** A 25-goose engine is a different feeding problem from a 7-animal herd, and ours does not scale to it.

Both are strategy changes, which is what 1.0.0 is for. Neither is a parameter.

## Round 7d: pricing the quadrant as an investment

`KAGG_LAND_PAYBACK` replaces "buy as soon as affordable" with a return test. When the gate is on, a quadrant is bought only if the profit its tiles can still return before the season ends clears its own price, and only if the cash left over still covers the animals the herd is missing.

The valuation, `_land_profit`, prices tile by tile on the glut curve each previous tile creates, counts only complete crop cycles that fit in the days left, and nets out seed cost. `_land_workable` caps it at the tiles the day has spare unit-turns for, charging each distant tile `KAGG_LAND_WALK` extra turns for the walk, since movement is 65% of all unit-turns.

### What the valuation says

```
day  5, 25 tiles   $50,504
day 10, 25 tiles   $27,460
day 15, 25 tiles   $24,642
day 18, 25 tiles   $24,642
```

Against quadrant prices of $1,000, $2,000 and $4,000, every quadrant clears its price by more than an order of magnitude. The gate fires on day 11 and buys all three in three consecutive turns.

### What actually happens

12 paired seeds against `champion` (frozen 0.22.0):

| Variant | Points | Seat-pair | Mean |
| :--- | ---: | ---: | ---: |
| payback gate, margin 1 | 0% | -23,404 +/- 2,305 | 30,808 |
| payback gate, margin 4 | 0% | -23,021 +/- 2,079 | 33,745 |
| payback gate, margin 10 | 0% | -17,480 +/- 1,468 | 41,667 |
| payback gate, walk 6, margin 10 | 0% | -11,866 +/- 3,139 | 45,019 |

The result is monotone in how hard the gate is to pass. Every knob that makes land rarer makes the agent richer, and the best configuration is the one closest to never buying.

### The gap is the finding

The model predicts a quadrant returns about $27,000. Buying it costs about $23,000 in final money. That gap is not a pricing error — the price model is the same one the planner already uses to choose crops, and it wins matches with it.

The gap is execution. `_land_profit` values a tile at what a *tended* tile returns. The diagnostic in round 7b showed 41 of the new tiles standing as weeds. The valuation is not wrong about what land is worth; the agent is wrong about what it can do with it.

### What would change the answer

1. Watering and harvesting routed as trips over clusters, not per-unit nearest-job, so a far quadrant costs one walk for five tiles instead of five walks.
2. An opening that reaches land through a cash crop instead of out of the herd budget.
3. Only then re-run this gate.

Round 8 did the first of those and land started to pay. Round 9 tested the second and it lost.

### Status: reverted

`main.py` went back to the submitted 0.22.0 byte for byte. The payback gate, the labour budget and their knobs were removed rather than left as dead switches.


# Round 8: routing, then land, from measured labour and 26 ladder replays

Tooling first, then four experiments, two of which failed. Every number is paired, seat-swapped, and scored against the regression pool unless it says otherwise. `tools/bandit.py` ran the sweeps: sequential halving with a floor of 40 paired seeds per arm in round one, ranked on the paired money difference, with the survivor confirmed on a fresh block against the standing default.

## What failed

**A sell schedule instead of hold-or-dump.** Releasing stock at the town's daily drain, with the excess spread over the days left, scored 62% points against the pool where the default scored 91%, about -$8,700 a seed. Gating the schedule on whether the town can absorb the backlog before the season ends recovered most of it but still lost: 84% against 91%. A glutted product never recovers, so holding it only sells the same units later at a lower price. Reverted.

**A denial term in the crop planner.** Pricing each tile at our revenue plus what our extra volume takes off the rival's realised price, weighted by their standing supply of that crop. It loses monotonically in the weight: 89% points at 0.5, 90% at 1, 89% at 2, against 91% for the default. Against a mirror the term is symmetric and changes nothing at all, byte-identical episodes even at weight 10. Against a varied pool it pushes the farm toward crops that are already crowded. Reverted.

## What worked

**Trip routing.** A unit now prefers work within two tiles over higher-priority work further away; an emergency watering still outranks the trip. Radius 2 beat radius 1 and 3 and beat a sticky-target variant, and confirmed twice on fresh seeds: +$3,901 +/- $1,837 and +$4,306 +/- $1,309. Against the frozen 0.22.0 it scored 60% points and +$1,775 +/- $557 over 200 held-out paired seeds. Movement fell from 65.0% of unit-turns to 56.8%, work rose from 20.9% to 22.0%, and idle rose from 14.1% to 21.2%. Frozen as `agents_0.0.x/v0_23_0_trips.py`, 0.23.0.

**Land, on the third attempt, once the labour number was measured.** `tools/labour.py` attributes every work action to the tile the unit stands on and divides by the days that tile was occupied:

```
occupant      ops/tile-day   hands/tile
COW                   3.22        0.134
SHEEP                 2.94        0.123
STRAWBERRY            1.37        0.057
MELON                 1.20        0.050
WHEAT                 1.50        0.062
```

`KAGG_HANDS_PER_TILE` was 0.34 for every tile. Crops need a sixth of that and animals about a third. That single wrong constant is what killed every earlier land test: `want_hands = min(MAX_HANDS, round(tiles * HANDS_PER_TILE))` asked for 17 hands on 50 tiles, and the Fibonacci hire cost makes that unaffordable, so the farm bought dirt it could not staff.

With the measured figure the joint sweep of land, hands and herd finally clears: one quadrant, `HANDS_PER_TILE` 0.2, 12 hands and a herd of 6 cows and 4 sheep confirmed at +$5,614 +/- $2,208 on 80 fresh paired seeds. Against 0.23.0 it scores 72% points and +$5,037 +/- $1,062 over 200 held-out seeds; against the submitted 0.22.0, 80% points and +$6,698 +/- $1,365 over 100 seeds. A second bought quadrant still loses at every hand count tried. Frozen as `agents_1.0.x/v1_0_0_land.py`, 1.0.0.

## What the ladder replays say

`replays_kaggle/` downloads, summarises and reads real episodes. Twenty-six of them, gzipped to about 0.4 MB each:

```
player                money  tiles  hands  weeds  top sales
ning gu              145025     75     14      2  melon 144, milk 237, strawberry 313
Toni Blanco          125048     75     13      0  egg 31, melon 60, milk 200
CompilingCoder        99602    100     13      1  carrot 32, melon 108, milk 159
Marcin Skalski        75880     25      8      7  melon 110, milk 118, strawberry 84
```

This refutes the conclusion round 7 drew from a single episode. The field does not run wheat and eggs: the top of this sample sells melon, milk and strawberry, the same premium goods we sell. What separates them is scale, 50 to 100 tiles against our 25 and 12 to 14 hands against our 8, and tending, with zero to two weed tiles where we carry three to eight on a quarter of the board.

Banning melon, which is what copying the round 7 winner implies, scored 15% points and -$33,366 in the joint sweep. The engine is not the problem; the acreage was.

## Discipline note

A herd of 6 cows and 6 sheep won all three halving rounds at 94%, 98% and 94% points, then failed its confirmation at -$874 +/- $3,170 and was dropped. That is the same league-overfitting failure the herd tournament produced in round 6, and this time the abort rule caught it before it shipped.

# Round 9: why a second bought quadrant still loses, and the herd bug it uncovered

Question from the replays: every strong player on the ladder buys land, most of them two quadrants, so what are we doing wrong at 75 tiles? One real bug came out of it, and five candidate fixes died.

## The bug: bought livestock that was never put down

A diagnostic on the day-by-day state showed 1 cow and 4 sheep sitting in unit inventories from day 12 to the end of the season, with five empty structures standing next to them and $34,000 in the bank. Units picked the animals up every morning, carried them all day, and dropped them back in the shed at nightfall.

`PLACE` sat at priority 7, below watering, harvesting and fertilizer collection, so a carrying unit always found something else to do first. `_animal_orders` then counted the carried animals as held stock and refused to buy replacements, so the herd froze at five of the ten it wanted.

`KAGG_PLACE_PRIORITY` already existed and had been rejected in round 6 at -$7,779. Under 1.0.0 it is worth **+$3,408 +/- $2,099** on 80 fresh paired seeds, 97% match points against 91%. What changed is the board: with a quadrant of land and a measured labour cost, a completed herd has somewhere to live and something to eat. The herd now reaches ten animals by day 24 instead of stalling at five.

Against the frozen 1.0.0 the fix scores 78% points and +$5,456 +/- $809 over 200 held-out paired seeds. Against the submitted 0.22.0, 88% points and +$11,283 +/- $1,606. Frozen as `agents_1.0.x/v1_1_0_herd.py`, 1.1.0.

## Why a second bought quadrant still loses

At 75 tiles the farm rots. Measured on the same seed, one bought quadrant against two:

```
              plants  animals  weeds  money day 16   final
one quadrant      32       10      5        $7,687  83,626
two quadrants     48       10     13        $1,851  70,599
```

Weeds are not overgrown empty tiles. Tracing every tile transition, 99 of the 127 weeds at two quadrants came from **plants dying of thirst**, against 45 of 53 at one. Death rates per tile-day: 3.1% in the home quadrant, 8.4% and 8.0% in the two bought ones. The leak is small and constant, one to seven plants a day going unwatered at nightfall, and it compounds, because a dead plant costs the crop, a `DIG`, and a replant.

## Five fixes that did not work

| Fix | Result |
| :--- | :--- |
| Water only when it pays yield, alternating days otherwise | Deaths unchanged, and a third of all plant-turns sit one dry day from death |
| One quadrant per unit per turn, weighted by the work in it | 32% points at two quadrants; re-assignment thrashes |
| Raise the hiring ceiling | 60% at 14 hands, 44% at 16. Hire cost is Fibonacci: 12 hands cost $376 a day, 14 cost $986 |
| Buy land earlier by cutting the seed reserve | No effect at all, at any reserve from $10 to $80 a tile |
| Open with wheat or carrot for early cash | Loses monotonically: 92% at six tiles, 88% at ten, 54% at fifteen with two quadrants |

The hiring result explains the fourth: the reserve never binds, because until the first melon harvest on day 10 the farm simply has no money. The fifth says the melon those tiles would have grown is worth more than the land they would have bought sooner.

## Geese, retested now that placement works

Round 7c's goose experiment ran with the placement bug in force, so the geese were bought and never put down. Retested with the fix: 72% points at four geese, 44% at eight beside eight cows, 32% at eight geese. The engine is not held back by placement alone.

## What the ladder actually does at 75 tiles

```
ning gu      145,025   75 tiles   61 plants   14 animals   0 weeds   str42 mel12 whe7
Toni Blanco  125,048   75 tiles   64 plants   11 animals   0 weeds   whe35 str28
Hongjie04     78,031   75 tiles   72 plants    3 animals   0 weeds   str48 mel16 tom7
chizhu        87,469   50 tiles   37 plants   13 animals   0 weeds   whe29 mel8
```

Zero weeds, all season, on a board twice ours. They buy the first quadrant between day 4 and day 10 and the second by day 11 to 13, each time spending down to a few hundred dollars, and they run 12 to 14 hands. The gap is not the crop mix and not the hiring ceiling; it is that they can keep 60 to 70 plants watered and we cannot. Until a unit's day is planned as a route rather than as a sequence of nearest jobs, the second bought quadrant stays a loss.

# Round 10: one experiment per idea, then the mixture

Eight single-idea sweeps under the 1.1.0 baseline, each a separate `tools/bandit.py` run with a floor of 40 paired seeds per arm and the survivor confirmed on a fresh block. The baseline was held fixed across all eight so the ideas stay comparable, and only then were the winners mixed.

| Idea | Result |
| :--- | :--- |
| Selling: exact sell order, partial scarcity, sell lots | Default wins. Exact sell order is a near no-op, the other two lose |
| Liquidation and cash floor | **`KAGG_LIQ_DAYS=6` confirmed, +$2,644 +/- $811** |
| Forecast factors: drain, future shops | Best arm failed confirmation at +$1,800 +/- $2,201 |
| Planner: integrated value, seasonal quotas, melon cap, projection | **`KAGG_EFFECTIVE_PROJECTION=0` confirmed, +$4,791 +/- $2,039** |
| Logistics: pickup budget, feeder units, feed days, shed target | Best arm failed confirmation at -$1,111 +/- $2,195 |
| Batching and ordering | **`KAGG_CARE_BEFORE_WATER=1` confirmed, +$2,652 +/- $2,080** |
| Melon race and opponent stock | **`KAGG_OPPONENT_STOCK=1` confirmed, +$4,533 +/- $1,559** |
| Hands: more hands, higher hiring ceiling | Default wins. 0.28 hands per tile with a 14-hand ceiling scores 79% |

The projection result is worth naming: charging fertilized output to the glut curve was a 0.22.0 improvement, and on a bigger board it now costs money. A parameter that paid on 25 tiles does not have to pay on 50.

## The mixture

Seven combinations of the four winners, halving down over 120 paired seeds and confirmed on 100 fresh ones:

```
LIQ_DAYS=6 + EFFECTIVE_PROJECTION=0 + OPPONENT_STOCK=1   +9,685 +/- 1,843   CONFIRMED
```

The three compose almost additively, +$9,685 against a sum of individual effects of about $11,900. `CARE_BEFORE_WATER` drops out: every mixture containing it scored below the same mixture without it.

Against 1.1.0 the mixture takes 80% and 82% of match points on two independent 100-seed blocks, at +$7,039 +/- $1,655 and +$7,738 +/- $1,593. On the regression pool it is positive against all eight opponents at 97% of match points. Frozen as `agents_1.0.x/v1_2_0_market.py`, 1.2.0.

## Two measurement repairs

The incident that cost this repository its uncommitted work also reverted `tools/bench.py` to an older regression pool: three superseded agents and three specialists, with neither the champion nor `v20_audit` in it. Every sweep in this round ran against that weaker pool, which is why match points saturate near 100% in the tables above. The pool now carries the champion, the earlier 1.x versions, the submitted 0.22.0, `v20_audit`, `v16_endgame` and the three specialists.

A first reading of the mixture against the champion reported +$77,553 with 400 wins in 400 games. It does not reproduce: two fresh 100-seed blocks give +$7,039 and +$7,738. The number was an artifact and is recorded here so it is not quoted later.

# Round 11: five shots at the tending gap, all missed

The ladder had just priced the problem, so this round aimed at it directly. Seventeen episodes of 1.1.0 gave 9 wins and 8 losses, and the day-20 picture separates them cleanly:

```
              day 20        wins            losses
   our plants               34.3            32.5
   their plants             26.7            40.9
   our weeds                 3.8             4.9
   their weeds               4.2             1.1
   our money              19,495          18,071
   their money            13,952          23,994
   their tiles              66.7            62.5
```

We beat the players who buy a bigger board and leave it half empty, and we lose to the ones who turn 62 tiles into 41 plants. Our own 50 tiles carry 33. So the target is productive tiles, not acreage, and not weeds on their own: mid-season weed counts are close, 4.3 against 2.8. The season-peak counts that look alarming, 14 against 13, are mostly the last three days when both sides stop tending.

Five experiments against the 1.2.0 baseline, each its own sweep, floor of 40 paired seeds, survivor confirmed on a fresh block:

| Idea | Result |
| :--- | :--- |
| Water the tiles where water buys yield first, ordinary survival watering after | Default wins the sweep outright |
| Cap planted tiles at 30, 34, 38 or 42 so the day can cover them | Best arm confirmed at +$462 +/- $1,550, dropped |
| Dig weeds sooner: priority 5, 6 or 7 instead of last | Best arm confirmed at +$1,247 +/- $2,045, dropped |
| Cap melon tiles at 8, 12 or 16 | Best arm confirmed at -$149 +/- $1,435, dropped |
| Plant sooner (priority 6 or 4) or free tiles by shrinking the herd | Best arm confirmed at -$178 +/- $2,251, dropped |

Nothing shipped. All four knobs were removed again and the default reproduces byte for byte.

The read: the knob-level tending ideas are exhausted. Watering earlier, digging earlier, planting earlier and planting less all fail because they move work between tiles without creating any, and the day is already full, with 57% of unit-turns spent on movement. The next real gain has to come from the structural change the 1.0.x plan has carried since the start: a unit's day planned as a route over a cluster, which creates tending capacity rather than reshuffling it.

# Round 12: three textbook mechanisms, none of them news to the planner

The question behind this round: the farm sells melon, strawberry and milk before anything else, and round 9's replay read already said that is not the mistake — the field sells the same three. But is "premium first" actually optimal, or just what a greedy per-tile pricing model happens to produce? Three ideas came from named results in resource-allocation and market-microstructure theory, each implemented as its own `KAGG_*` flag and swept with a floor of 40 paired seeds, survivor confirmed on a fresh block, same discipline as round 10 and 11.

| Idea | Mechanism | Result |
| :--- | :--- | :--- |
| `KAGG_HERD_WATERFILL` | Let livestock bid for a tile against every crop by the same $/tile-day the planner already prices crops with, instead of reserving the first N empty tiles for the fixed herd list | Round 1 arm led at +$29,238 vs +$25,840 (91% vs 90%), confirmed at **-$1,003 +/- $1,935**, dropped |
| `KAGG_COURNOT_PLANT` | Price a planting at the inventory both farms' standing tiles will have pushed it to by harvest, not just the town's drain, so a crop the rival is already growing heavily prices lower before it actually gluts | Round 1 arm led at +$26,137 vs +$25,840 (88% vs 90%), confirmed at **-$2,641 +/- $2,110**, dropped |
| `KAGG_AC_LIQUIDATION` | Replace the uniform end-game sell-down (`kept_total / days_left`) with a front-loaded hyperbolic schedule, `frac = 1 - sinh(kappa*(d-1))/sinh(kappa*d)`, on the theory that the risk being hedged is the rival dumping first, not price volatility | Default won round 1 outright, confirmed at **+$0 +/- $0**, dropped |

All three flags were removed again; the default reproduces byte for byte.

**Water-filling and the Cournot term chase noise the model already resolves.** Both ideas assumed the planner is leaving money on the table by not accounting for something — the herd's own diminishing marginal value, the rival's standing supply. But `_crop_value` already quotes each tile at the inventory its own prior tiles this turn pushed it to, which is a one-farm water-fill by construction, and the herd reservation is capped at ten animals precisely because the animal-value curve saturates fast. Neither addition moved the *outcome* mix enough to clear noise at 100 seeds; both pointed the wrong way on the confirmation block, which is what an idea correcting a real but small mispricing looks like next to an idea correcting nothing.

**The liquidation curve is rarely the binding constraint.** At kappa 0.3 the front-loaded schedule does ask for meaningfully more than a sixth of the shed on the first day of the six-day window — the CI being exactly $0 wide says the two agents played identical actions on most of the confirmation block. `_hold_quota`'s cash-needed and shed-capacity forced sales, plus the opponent-dump threshold from round 10, already clear most held stock before the linear or the hyperbolic ramp is ever consulted. A schedule only matters when something is actually being held back, and by day 24 little is.

**The standing answer holds.** Round 9's replay read said the gap to the top of the ladder is acreage and tending, not crop selection, and this round is the same conclusion from the opposite direction: three mechanisms built to make crop and herd selection smarter than "premium first, land permitting" found nothing to correct. The planner's greedy per-tile pricing is already close enough to the theoretical water-fill and Cournot-adjusted solutions that the gap doesn't clear measurement noise.

# Round 12: the day plan that finally pays, and a benchmark that was lying

The ladder had said the same thing four times: in every loss the opponent held 75 tiles and we held 50, while our own utilisation was the better of the two, 84 to 96 per cent against their 56 to 91. Copying their composition failed once more first. A wheat rotation reserve, which is what the two 120,000-dollar opponents ran, scored +$1,223 +/- $2,116 on its own and lost monotonically beside a second quadrant: 72% of match points at twelve wheat tiles, 51% at twenty, 34% at thirty. A herd of 22 or 28 animals on two quadrants, which is what Zubatch ran with 29, scored 36% and 16%.

## The day plan

`ROUTE_CLUSTERS` gives each unit a strip of the working board for the whole day. Three details decide whether it pays:

- Split the tiles that carry work, not the acreage. Splitting the board evenly leaves a unit standing in bare ground while the next strip drowns.
- Fix the assignment for the day. Recomputing it per turn is what made the quadrant experiment in round 9 thrash at 32% of match points.
- Make the strip a preference, not a fence. A hard restriction leaves a unit fiddling with a low-priority job while a plant dies two strips away; a one-tile distance penalty keeps priority global and still holds units in their neighbourhood.

Confirmed at **+$7,140 +/- $2,085** against the pool, 98% of match points against 92%. Against the frozen 1.2.0 it takes 75% of match points and +$4,476 +/- $834 over 200 held-out paired seeds, and it is positive against all nine pool opponents at 97%. Frozen as `agents_1.0.x/v1_3_0_clusters.py`, 1.3.0.

## The benchmark was measuring against `starter`

Twice in this session a run reported a four-figure win rate: 400 games to nil, +$77,553, and later 200 to nil at +$78,642. The first was recorded as an unreproducible artifact. It was not an artifact. `tools/bench.py` defaults its opponent when none is named, and the copy that came back through a merge defaults it to `starter` rather than `champion`. Every bench call without an explicit opponent was scoring against the tutorial agent, whose mean is about 3,500 — exactly the opponent mean those runs reported.

Nothing that shipped rests on those numbers: every version gate in rounds 8 to 12 was either a `bandit.py` confirmation against the pool or a bench call with the opponent named. The default is now `champion` again.

The lesson for the log: a benchmark that cannot lose is not reporting a strong agent, it is reporting a weak opponent. The header line names the opponent on every run, and it should be read.

## One more repair

Editing `main.py` while a sweep runs in the background makes the worker processes import a half-written file. It produced a round of results where every arm, including the unmodified default, scored 1% of match points. Those results were discarded and the sweep re-run on a stable tree.

# Round 13: the herd the day plan can now afford, and why feed is bought

The three losses under 1.3.0 all showed the same two columns: they held 75 or 100 tiles against our 50, and they sold 194 to 298 fertilizer against our 95. Fertilizer is free — an animal makes one a day and nothing else in the game consumes it — so the second column is a headcount, not a policy. They ran 13 to 15 animals; we ran 10.

A herd of seven cows and five sheep confirmed at **+$4,106 +/- $2,307**, 97% of match points against 92%, and holds at 67% and +$3,718 +/- $1,019 against 1.3.0 over 200 held-out paired seeds. The same size lost in round 10 under 1.1.0. What changed is the day plan: twelve animals need feeding and collecting every day, and that is only affordable once a unit works a neighbourhood instead of crossing the farm.

Collection itself is not the gap. Measured over one season: 206 animal-days, 162 collections, 59 of those spent fertilizing crops, 102 sold. Promoting `COLLECT_FERTILIZER` above harvesting was swept and lost.

## Why the feed is bought rather than grown

The agent buys 168 wheat a season, about $7,158, at a price that climbs from $33 to $53 because both farms buy feed while the town drains it. Growing it looks obvious and it is not:

| Variant | Points |
| :--- | ---: |
| Eight tiles of wheat | 74% |
| Eight tiles of wheat, six days of feed reserve | 50% |
| Six days of feed reserve alone | 46% |
| A second quadrant with eight tiles of wheat | 40% |
| A second quadrant, twelve tiles of wheat, six days of reserve | 20% |

Eight wheat tiles do cut the feed bill, from 168 units to 132, about $1,600. They cost more than that in premium tiles displaced.

The reserve result has a harder cause, and it is the shed. Capacity is 100 items and **everything above it is discarded at the end-of-day drop**. Peak shed occupancy per three days, one season:

```
default, two days of feed     3   9  12  17  33  38  43  82  77  59
six days of feed              3  21  21  28  55  66  96 100 100 100
six days plus own wheat       5  16  47  60  68  97 100 100 100 100
```

A longer feed reserve pins the shed at the cap for the last third of the season, so every harvest after that is thrown away, and purchases are refused for three times as many turns. The two-day reserve is not an oversight; it is what the shed can hold.

Frozen as `agents_1.0.x/v1_4_0_herd.py`, 1.4.0.

## Strategies from the ladder that still do not transfer

A wheat-first farm, which two of the 120,000-dollar opponents run, was played head to head against the champion over three seeds:

```
all wheat, fixed planner        12,325  vs  90,547
wheat 3 : strawberry 1          18,838  vs  77,534
dynamic planner, melon banned   43,329  vs  68,204
default                         53,393  vs  54,606
```

The volume crops only pay on a board we cannot yet keep alive.

# Round 14: the farm was laid out backwards

Read off the replays, at day 20, mean walking distance from the shed:

```
player                money  animals  animals@   crops@
Jeff Marc E. Cadet  156,992       18       1.3      4.4
Rohan Jain          154,165       16       1.0      3.6
YoungCheol Son      145,122       14       1.2      4.7
ning gu             145,025       14       1.2      4.6
Toni Blanco         125,048       11       1.8      4.0
Ashutosh Ghodasara  123,188       10       1.2      4.2
us (1.4.0)           53,432       11       5.9      3.3
```

Every strong player keeps the herd against the shed and farms the far tiles. We did the exact opposite: animals at 5.9, crops at 3.3.

The mechanism is in the rules rather than in the prices. An animal wants three or four actions a day, and **every feeding starts with a PICKUP at the shed**, so a distant animal costs a round trip per feed. A crop wants one or two actions and carries nothing. Distance is cheap for a plant and expensive for an animal, and we had it the wrong way round.

`KAGG_NEAR_SHED_HERD` already existed, off by default, from an early round that tested it before there was land, a day plan or a twelve-animal herd. Turned on now it moves the herd to 2.3 from the shed and the crops out to 4.6, and against 1.4.0 it takes **82% of match points, CI 78 to 87, at +$4,925 +/- $712** over 200 held-out paired seeds. On the pool it is positive against all eleven opponents.

Frozen as `agents_1.0.x/v1_5_0_layout.py`, 1.5.0.

## The sweep nearly threw it away

`bandit.py` ranks arms on the paired money difference, and on that statistic the default beat this change in both rounds: +$28,073 against +$26,802, then +$26,974 against +$24,749. Match points said the opposite in both rounds, 94% against 88% and 92% against 84%, and the head-to-head against the champion settled it at 82%.

The pool is nine opponents, most of them superseded versions that lose badly. Money against a weak field is dominated by how large the blowouts are, and the layout change trades a little of that for winning more games against a strong one. The competition scores wins.

Ranking on money was adopted in round 10 for a good reason — it is the lower-variance statistic and the sign test throws information away. It is still right for tuning a knob. When money and points disagree across rounds, the head-to-head against the champion is the tiebreak, because it is the only one of the three that matches how the ladder scores.

# Round 15: the second quadrant stops being a labour problem and becomes a market one

With the day plan and the herd beside the shed, two quadrants were retested from the 1.5.0 baseline. One seed, side by side:

```
              day 20                     final
50 tiles      37 plants, 0 weeds, 32,771     93,000
75 tiles      57 plants, 5 weeds, 25,978     70,498
```

The farm now works the bigger board: fifty-seven plants standing and almost no weeds, where round 9 measured forty-eight plants and thirteen to twenty weeds. The money went the other way, and the crop counts say why. The extra tiles went to strawberry, thirty-six against twenty-three, and strawberry carries `above_target 1.60`. We stopped losing the tiles and started drowning the price.

Five attempts to price that better, all against the pool with a floor of forty paired seeds:

| Attempt | Points |
| :--- | ---: |
| Two quadrants, unchanged | 55 to 66% |
| Charge fertilized output to the glut curve again | 60% |
| Price the whole harvest down the curve, not the marginal unit | 57 to 60% |
| Believe half the town drain instead of a quarter | 50% |
| Ten to twenty-four tiles of wheat on the far quadrant | 45 to 57% |
| Three quadrants | 32% |

## Pricing a tile against our own standing harvest

`_dynamic_plan` quotes each tile at the market it will sell into and walks the quote down the curve for every tile it allocates in the same pass, which is what keeps a mix mixed. It had one blind spot: the quote starts from today's market inventory and ignores the harvest already growing on our own tiles. Planting the thirty-first strawberry was priced as if the thirty already in the ground did not exist.

Closing that — seeding the projection with `_farm_supply` over our own tiles — is more correct and does not pay: +$1,529 +/- $1,621 at best, interval across zero, and 59% of match points with a second quadrant. Reverted, and recorded because the reasoning is sound and the next person will think of it again.

## Where this leaves the acreage question

The ladder gap is real and unchanged: every strong opponent holds 75 or 100 tiles and we hold 50. What changed this round is the reason we cannot follow them. It is no longer that the far tiles rot. It is that our product mix has nowhere to put the volume, and the volume crops that would absorb it — wheat above all — are worth less per tile than the premium crops they displace, at every share tried.

Two ways out, neither tested yet. Sell into more products at once, so no single curve takes the whole harvest, which means a genuinely wider mix rather than a wheat quota bolted onto the same planner. Or find the execution the ladder leaders have that lets a hundred tiles of strawberry and milk clear at a price we cannot reach.

# Round 16: measuring the loss instead of counting weeds

Weeds are a symptom and a poor one: a tile that dies empty costs nothing, and most of the season-peak weed count comes from the last three days when both farms stop tending. `tools/losses.py` counts units instead — yield that died with a plant, yield still standing when the season closes, produce discarded by the shed, and animals that starved and left.

One seed, one quadrant against two:

```
                                  50 tiles   75 tiles
units lost with dead plants             14         37
units left standing at close            27         42
units discarded by the shed              6          0
animals starved and left                 0          0
```

About 32 units of extra loss, call it $8,000. The gap on that seed is $22,000, so the losses are real and are not the story.

The story is in what never got produced. With the same twelve animals on both boards we sold 185 milk and 129 fertilizer on 50 tiles, and 151 milk and 102 fertilizer on 75. Prices were within a few percent of each other, so this is not the market saturating, which is what round 15 concluded and got wrong. The extra crops take unit-turns from the herd, and an animal that misses its `CARE` loses a unit on its next production.

Promoting animal work directly does not recover it: with two quadrants, collecting before harvesting scored 60% of match points, caring before watering 57%, both together 48%, against 91% for the one-quadrant default.

## Two angles closed

The ten-order cap never binds. Across a full season the agent places 0.78 orders a turn and reaches ten on zero turns of 719, so nothing is being truncated.

The strongest opponent on the ladder is not selling cleverly. Reading episode 95178049 day by day, Rohan Jain and this agent both hold strawberry until day 20 to 22 while the price climbs from 128 to 268, then unwind into it. He sells 18, 42, 40, 50, 37, 26 on the even days; we sell 23, 24, 29, 29, 17. Same timing, same curve, twice the volume, because he farms twice the board. There is no sell-side trick to copy.

## Leftovers swept

Against the 1.5.0 baseline: the melon race scored 79% at either threshold, selling in lots of twelve 78 to 85%, and holding everything until liquidation 12%, all against 90 to 94% for the default. The melon race, unresolved since round 6, is now resolved: it loses.

# Round 17: an equal-acreage loss, and why we cannot copy the herd that caused it

1.5.0 rates 744.5, the best so far, ahead of 1.3.0 at 719.1 and 0.22.0 at 648.4, and takes 15 wins in 28 ladder games with a mean of 75,235 against 71,372.

Most losses still come from opponents holding 75 or 100 tiles. Episode 95267217 does not, and that makes it the useful one: Thái Phạm Công won 119,496 to 82,996 on **fifty tiles each**. The difference was the split between crops and animals.

```
day 16          us      them
plants          36        34
animals         12        16
day 24 plants   37        26
day 24 animals  12        17
sold fertilizer 103       256
sold milk       136       209
sold wool        67       168
```

They gave tiles to animals as the season went on; we kept planting. Fertilizer is free and nothing drains it, so a bigger herd is a larger free income, and milk and wool sit on curves our volume never reaches.

## Copying the herd size fails, and the reason is delivery

Head to head against 1.5.0 over 60 paired seeds:

```
nine cows, seven sheep              -8,087 +/- 1,857
nine cows, seven sheep, two geese  -19,906 +/- 2,323
eleven cows, nine sheep            -12,888 +/- 1,884
```

The day-by-day trace says why. With sixteen animals the wheat store runs to 2, 3 and then 0 from day 24, and nine or ten animals go unfed with eleven to sixteen missing their `CARE`. None starve — they simply stop producing for the rest of the season.

The feed logic is what starves them. `_feed_orders` targets `herd * FEED_DAYS` and `CARRIED_FEED` counts wheat already in unit inventories toward that target. With twelve units each holding two or three wheat, the accounting reads about thirty units in stock against a target of thirty-two, so the agent buys two at a time while the wheat never reaches the animals. The stock is real; the delivery is not.

Three fixes, all measured head to head:

| Fix, with sixteen animals | Result |
| :--- | ---: |
| Stop counting carried wheat as stock | -$14,000 area, worse |
| Four days of feed reserve instead of two | worse still |
| Four dedicated feeder units | -$91 +/- $2,799, parity |

Dedicated feeders close almost the whole eight-thousand-dollar gap, which confirms the diagnosis, and still do not make the bigger herd worth having.

On the standing twelve-animal herd, four feeder units scored +$1,668 +/- $943 on one hundred seeds and +$1,065 +/- $1,069 on a fresh block. The interval crosses zero on the confirmation, so under the acceptance rule it is dropped rather than kept.

## Two more angles closed

The shed target does not block feed buying. Raising the cap for feed purchases from 70 to the full 100 produced a byte-identical episode.

`replays_kaggle/summarize.py` now tolerates a null entry in a market order list. Some ladder agents emit one, and it crashed the summariser on the forty-four episodes pulled this round.

# Round 19: the melon thesis, priced properly and measured

Melon looks like the worst-valued crop we grow. It appears in **no shop**, so the only drain is the town centre at one unit a day, thirty for the season. Its glut curve is `sq` at `above_target 3.60`, the steepest in the game, over a depth of three hundred shared with the opponent. Round 7 traced it from 256 to 13 by day 15; we sell ninety to a hundred and thirty a season at an average of forty-two against a peak of two hundred and seventy-two. The seed costs eighty and locks a tile for ten days while we hold under six hundred dollars through day eight.

Two independent attacks, both measured, both dropped.

## Replacing a crop a successor would out-earn

`DIG` removes a live plant in one action, so a crashed melon can be cleared and the tile reused mid-season, when labour is not yet the binding constraint. The rule compares the standing crop's remaining earnings per day against the best alternative that fits the days it would free.

Two versions were needed before the measurement meant anything:

- The first compared the standing crop at **today's** price against a replacement priced at the market it would sell into, and excluded nothing. It dug freshly sown strawberries to plant strawberries — a hundred and fifty of them — and scored 23,815 against 58,390 on a single seed.
- The second quotes both sides the same way and only counts a **different** crop whose lifespan fits the freed days.

The corrected rule still fires on young strawberries rather than crashed melons, because it weighs a marginal standing tile against the *first* tile's rate for the alternative, while the planner walks that rate down the curve for every tile it allocates. Swept at thresholds 1.0, 1.2 and 1.4 against 1.6.0, the best arm confirms at **+$265 +/- $1,005**. Dropped.

## A finance-shaped valuation: horizon, discount and certainty equivalent

`_crop_value` is `(units * price - seed) / LIFESPAN`, which is an equivalent annual annuity at a zero discount rate — the standard way to compare projects of unequal lives, and the right family. Three things it omits, each added as its own knob:

- **Finite horizon.** Over the days that remain a tile is worth the whole cycles that fit, not a rate carried on for ever. At day 14 melon fits once and leaves five idle days; carrot fits five times and leaves none.
- **Discount rate.** A dollar on day ten buys the next animal; the same dollar on day twenty-six does not.
- **Certainty equivalent.** Quote the sale with the rival's standing crop already on the market. Their tiles are public and `_farm_supply` returns them exactly. Melon takes the deepest cut.

Against 1.6.0, first round of forty paired seeds each:

| Arm | Points |
| :--- | ---: |
| Default | 91% |
| Rival risk 0.5 | 90%, then 95% |
| Horizon value | 96% in round two |
| Discount 0.03 | 82% |
| Rival risk 1.0 | 86% |
| Rival risk 1.0 with discount 0.03 | 82% |
| Discount 0.08 | 75% |

The survivor, a half-weight certainty equivalent, confirms at **+$550 +/- $727**. Dropped.

The pattern across both attacks is the same one round 15 found when seeding the projection with our own standing harvest: the planner's marginal pricing already does most of this work, and every more-correct valuation bolted on top lands inside the noise. The melon diagnosis is right about the crop and wrong about the lever — the loss is realised at the point of **sale**, not at the point of planting, and `_sell_orders` already holds or dumps per product on the forward supply forecast.

All four knobs were removed and `main.py` reproduces 1.6.0 byte for byte.

# Round 20: does livestock land compete with crop land

A rules audit against the upstream source (`kaggle_environments/envs/kaggriculture/kaggriculture.py`, not just `RULES.md`) turned up one documentation bug and no agent gap: `RULES.md`'s shop table is missing `WHEAT` on the Ice Cream Shop, but both the real source and `main.py`'s own `SHOPS` dict already have it. Everything else cross-checked — wheat-for-feed, fertilizer-from-animals, the CARE payout rule, the single-product-shop 2x multiplier — was already handled correctly.

The one open question the audit raised: `_dynamic_plan` reserves the herd's tiles by a fixed count from day 0 (`NEAR_SHED_HERD`), never bidding them against crops the way `_crop_value` prices crop tiles. Three angles, all measured with `tools/bandit.py` against the pool.

## Herd size: the reservation is undersized, not oversized

Sweeping `KAGG_HERD_SPEC` down from the standing `COW:7,SHEEP:5` (40 paired seeds, round one):

| Herd | Diff vs pool | Points |
| :--- | ---: | ---: |
| `COW:7,SHEEP:5` (default) | +26,772 | 91% |
| `COW:5,SHEEP:4` | +26,062 | 84% |
| `COW:3,SHEEP:2` | +17,801 | 78% |
| no herd | -9,693 | 15% |

Shrinking the herd loses monotonically, so livestock is not crowding out crop land today. Sweeping up found the opposite: `COW:8,SHEEP:7` (15 animals) beat the default in round one and **confirmed on a fresh 100-seed block at +$3,959 +/- $2,406, 92% vs 86% points**. Cheap, real, not yet applied to `main.py`'s default.

## Delaying the reservation (`HERD_START_DAY`) loses, but for the wrong reason

`HERD_START_DAY=2/4/6` all scored **identically** — 60% points, +14,529 diff, byte-for-byte the same across all three — against the default's 91%. The knob does not do what it sounds like: `_animal_orders`'s `wanted` is read from the current tile plan, so before the threshold day no tile is animal-designated and **no animal is bought either**, not just unplaced. The three settings tie because whatever day the first quick crop is actually harvested is the real gate, not the knob. Delaying purchase by 2-6 days of a season where animals produce "forever while fed" costs more than the idle early tiles were worth. Dropped as tested; the buy-timing and tile-timing decisions need to be separated to test the real hypothesis.

## Decoupling buy timing from tile timing (`KAGG_EAGER_HERD`), built and dropped

Built in worktree branch `worktree-eager-herd` (not merged): `_animal_orders` requests the full herd deficit (`_herd_deficit`) from day 0 regardless of the tile plan, and `_dynamic_plan` only converts a tile to a structure once an animal is actually sitting bought-and-unhoused in the shed, claiming the nearest currently-empty tile to the shed rather than a tile reserved in advance. Land fills with fast crops first; animals move in as land and cash allow.

Two iterations, both against the pool, both losing:

| Variant | Points | Diff vs pool |
| :--- | ---: | ---: |
| v1 — first empty tile in board order | 57% | -14,000-ish |
| v2 — nearest-to-shed empty tile (matches `NEAR_SHED_HERD` geometry) | 64% | -13,317 |

`tools/losses.py` on seed 1 vs champion explains it: dead-plant tiles rose from 48 to 65 and shed overflow appeared (8 units discarded, was 0) under the eager variant. The tile a unit is allowed to work is fixed for the whole day by 1.3.0's day-plan/routing system; letting `_dynamic_plan` flip empty tiles between crop-eligible and animal-eligible on every call reshuffles that working set mid-day, the same thrashing round 9 already measured at 32% for a different mid-day-replan idea. The idea needs a routing fix — freeze the working-tile geometry once a day, same as everything else does — before it can be measured fairly. Not attempted this round.

**Kept: nothing applied yet.** `COW:8,SHEEP:7` is a confirmed, low-risk win sitting unshipped. The eager-buy idea is real but blocked on the day-plan system, not on the herd-vs-crop economics.

# Round 20: what a tile actually costs

`_crop_value` charges one thing: the seed. Two real costs never appear in it.

**Fertilizer.** For the ongoing crops the valuation doubles `units`, which is the benefit of fertilizing, and never charges the price. Fertilizer is free from the herd, but it is also a good we sell — ninety-five to a hundred and three units a season — so every application is a sale forgone. Strawberry needs about two applications to take four units to eight, roughly $120 at the traded price against a seed of $100. The cost basis of a strawberry tile is more than double what the planner believes.

**Labour.** `tools/labour.py` measured work per tile-day per crop in round 8: wheat 1.34, tomato 1.29, strawberry 1.26, melon 1.19, carrot 1.11. Multiplied by occupancy that is twenty unit-turns for a strawberry against three for a carrot, a seven-fold spread the valuation ignores entirely. Round 7c built a labour *budget*, a cap on the total, and reverted it at +$576 +/- $1,282; charging labour as a *cost* changes the ranking between crops instead of capping the sum, which is a different thing and had not been tried.

Both were added and swept against 1.6.0. On a single seed all three arms beat the default handsomely — 86,165 and 96,603 against 62,825 — which is exactly the kind of result the confirmation step exists to catch.

| Arm | Points |
| :--- | ---: |
| Fertilizer charged | 94% in round two |
| Wage 5 per unit-turn | 92% |
| Fertilizer plus wage 10 | 88% |
| Default | 91% |
| Wage 10 | 91% |
| Wage 20 | 92% |

The survivor, charging fertilizer, confirms at **-$395 +/- $2,013**. Dropped.

This is the fourth valuation refinement in three rounds to land inside the noise, after our own standing supply in round 15 and the horizon, discount and certainty-equivalent terms in round 19. The planner's marginal pricing — walking the quote down the glut curve for every tile it allocates in the same pass — appears to dominate whatever these terms add. The remaining lever on crop choice is not a better price for a tile; it is what happens to the harvest afterwards.

# Round 21: the farmer worked the last day alone

Two observations from the replays, one worth $1,356 a match.

## Nobody was hired on day 29

The hiring gate reads `hour < HIRE_HOURS and day < LAST_DAY`. The last day was excluded because hands are hired for a single day and that day looked spent. It is not: it is the richest harvest of the season, and it was being worked by the farmer alone.

Tracking standing yield through the close, on one seed:

```
day 27 hour  0   55 units standing      hour 22   14
day 28 hour  0   42 units standing      hour 22    8
day 29 hour  0   29 units standing      hour 22   29
```

Days 27 and 28 are cleared. Day 29 does not move at all, because one unit cannot reach twenty-nine units of yield spread over fifty tiles. Twelve of those were strawberry at about $200 and eight were milk and wool.

Hiring on the last day as on any other costs $376 for twelve hands and takes the standing yield from twenty-nine units to five. It confirms at **+$1,356 +/- $166**, 97% of match points against 93%, and holds at +$1,142 +/- $117 against 1.6.0 over 200 held-out paired seeds — the tightest interval in the log. On the pool it is positive against all thirteen opponents.

Frozen as `agents_1.0.x/v1_7_0_lastday.py`, 1.7.0.

## Tiles held for animals we cannot afford

Through day 8 the farm holds five bare tiles and two to five empty structures — seven to ten of twenty-five doing nothing — on a balance of $150 to $550. The strong ladder players have nineteen crops standing at day 2 where we have thirteen.

`NEAR_SHED_HERD` reserves all twelve herd positions on the first turn, whatever the balance. Releasing the ones the money cannot reach for another few days, and lending them to a crop short enough to be gone before the animal arrives, does exactly what it promises: crops standing at day 2 go from 13 to 19, bare tiles from 5 to 1, and the herd completes one day later.

It still loses. Swept at lead times of 2, 3, 4 and 6 days against 1.6.0, the default won its own sweep outright. A first version without the short-crop restriction was far worse — it lent the tiles to strawberry, which occupies them for sixteen days and strands the animal.

The board looks better and earns less. What the extra early crops return does not cover the herd arriving late, and the herd is where the compounding is: an animal bought on day 6 produces for twenty-three days.

# Round 22: the idle opening, diagnosed properly

The replays keep showing the same opening: through day 8 the farm holds five bare tiles and two to five empty structures, seven to ten of twenty-five doing nothing, while the strong ladder players have nineteen crops standing at day 2 against our thirteen. Measured over days 0 to 12 that is **131 idle tile-days**, which at thirty to a hundred dollars a tile-day is worth more than any single change shipped this week.

Three explanations were tested. The first two were wrong about the cause.

## It is not the reservation, and it is not the order

Round 21 released the herd tiles the balance could not reach. It lifted day-2 crops from 13 to 19 and lost anyway, because the herd completed a day later.

Filling the herd cheapest-first changes nothing: `COW:7,SHEEP:5` is already in cost order, and reversing it to dearest-first loses outright.

Ordering animals for *planned* tiles rather than built structures — cows and sheep share the pasture, so cows filling every built pasture could in principle starve the sheep of orders — produces a byte-identical episode.

## What the trace actually says

Instrumenting `_animal_orders` day by day:

```
day  0  wanted SHEEP 5, COW 7   money 1,560   buys COW 3
day  4  wanted COW 4, SHEEP 5   money   154   buys nothing
day  9  wanted COW 1, SHEEP 5   money  -207   buys nothing
day 12  wanted SHEEP 2          money 6,973   buys nothing
day 16  wanted nothing          money 18,434
```

The day-12 line looks like the bug — seven thousand dollars in hand and nothing bought — and it is not. Those two sheep were bought earlier and are sitting in the shed waiting to be placed, so `need - shed` is correctly zero. The herd is complete by day 16.

The real picture is simpler and harder: the opening float sits between **-$207 and +$154 for nine consecutive days**. The farm is not failing to spend; it has nothing to spend. Every animal is bought the moment the money exists, one at a time, and the tiles wait because a cow costs $400 against a balance that hovers at zero.

## Cheap animals to fill the gap

If the gap is cash, the cheapest animal should close it: a goose is $300 against a cow's $400 and yields from day 4 rather than day 8. Swept against 1.7.0 with two geese added to the herd in three configurations, plus a goose-free control at eight cows and four sheep:

The survivor was the goose-free `COW:8,SHEEP:4`, confirming at **+$474 +/- $2,259**. Every configuration containing geese lost the first round.

## Where that leaves it

The 131 idle tile-days are real and remain unrecovered. What the round rules out is that they are caused by the reservation policy, the fill order, the purchase logic, or the choice of animal. They are caused by the opening cash curve: melon, our best crop, pays nothing until day 10, and until then the farm is genuinely broke.

That points at the opening mix rather than the herd, and round 18 already measured the obvious version of that — sowing cheap seed to plant more tiles — as a loss. The lever, if there is one, is a crop that pays *before* day 10 without displacing the melon that pays after it.
