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
| v4 `main.py` | Animal support added; **off by default**, it loses | same as v3 |

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
