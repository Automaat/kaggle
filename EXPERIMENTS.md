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
| v2 `main.py` | Crop mix `MELON:4,STRAWBERRY:1,CARROT:1` | 20/20, mean $36,868 |

v2 beats v1 20/20 ($34,914 vs $5,266).

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

## Ideas rejected

- **Pure melon.** Loses to any melon/strawberry blend: 300 melons of capacity
  against a floor that arrives at about 150 units.
- **Tomato and strawberry as the core.** 0.33 and 0.24 units/tile/day is too
  little throughput; the tiles sit occupied for 11 and 16 days.
- **Unconditional holding.** See v1 above.

## Not tried yet

- **Buy land.** NE $1,000, SW $2,000, SE $4,000. v2 ends with $37k unspent, so
  this is almost certainly free money. Likely the next biggest win.
- **Animals.** Goose is 1.00 units/tile/day forever and egg has a `log` 0.20 glut
  curve, so eggs barely crash. The catch is 1 wheat per animal per day; needs a
  wheat block or market buys costed out.
- **Fertilizer.** Doubles the watering bonus for 3 days: wheat 4 -> 6, carrot
  3 -> 4, melon reaches its cap at age 8 instead of 10. Animals also produce it
  free via `COLLECT_FERTILIZER`.
- **Sell into scarcity spikes.** Carrot, tomato and egg use the `hinge` curve, so
  their prices run away once town demand passes `T`. Watch `unlocked_shops` and
  plant into whatever the town just started eating.
- **Opponent modelling.** Both farms are visible. Counting the opponent's melon
  tiles predicts the coming glut and should shift our sell timing.
- **Care and fertilizer actions for animals**, `CARE` banks +1 per fed day.
- **Parameter tuning with CMA-ES** over the mix weights, shed target, drift
  window and cash floor, against a pool of frozen agents.
