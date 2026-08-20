# Kaggriculture rules

Repo-local copy of the rule set. Upstream truth is `kaggle_environments/envs/kaggriculture/` (`README.md`, `AGENTS.md`, `kaggriculture.py`), which lives in `.venv` and is lost on a clean install. Facts here were read off that source; the last section holds rules found by experiment instead of by reading.

## Shape of a match

Two players, one farm each, 30 days of 24 turns = 720 turns. Each player starts with $3,000. Most money in the bank at the end wins; ties are possible. Unsold goods score nothing.

| Config | Default |
|:---|---:|
| `episodeSteps` | 720 |
| `boardSize` | 10 |
| `startingMoney` | 3000 |
| `maxMarketOrdersPerTurn` | 10 |
| `turnsPerDay` | 24 |
| `shedCapacity` | 100 |
| `weedSpawnChance` | 0.005 |
| `townShopUnlockInterval` | 3 |
| `townShopSellInterval` | 4 |
| `townCenterSellInterval` | 24 |
| `seed` | null |

Seed costs, animal costs and base prices are fixed and cannot be configured. Per-resource market curves can be overridden with `marketParams`.

## Board

- Each player owns a private 10x10 grid, split into four 5x5 quadrants.
- Only NW starts unlocked. `BUY_LAND` unlocks NE, then SW, then SE, in that fixed order, for $1,000 / $2,000 / $4,000.
- A tile is `None` (empty), `"LOCKED"`, a plant dict, a weed dict, or a coop/pasture dict.
- Locked tiles are passable. Tile actions no-op there and cost nothing.
- Every empty unlocked tile has a 0.5% chance per day of growing a weed. `DIG` clears it.

### Shed

- The shed sits at the centre and is not a tile. It never appears in `tiles`.
- Shed-adjacent means standing on `(4,4)`, `(5,4)`, `(4,5)` or `(5,5)` — one tile in each quadrant. Three of the four start locked, but shed actions work from a locked tile because they never touch the tile.
- Capacity 100 non-seed items. Seeds live in a separate slot with no cap.
- Overflow past the cap is discarded, not held. This applies to `PLACE`, `BUY_PRODUCT`, `BUY_ANIMAL` and the end-of-day drop.

### Units

- One permanent farmer, plus farm hands hired for one day.
- `HIRE` costs `fib(n)` where `n` is the count already hired today: 1, 1, 2, 3, 5, 8, 13, 21. The counter resets each day.
- Hands spawn on the least-occupied shed-access tile, ties broken NWSE. Locked is ignored, so the first hire of the day lands on `(5,4)` while NE is locked.
- Every unit acts once per turn, independently.
- At end of day all units respawn at `(4,4)`, hands vanish, and every inventory is dumped into the shed.
- Units may share a tile.

## Actions

Action dict per turn:

```py
{
  "farmer": [op, ...args],
  "hands":  [[op, ...args], ...],
  "market": [[op, ...args], ...],
}
```

Invalid actions are silent no-ops.

### Unit ops

| Op | Effect |
|:---|:---|
| `NORTH` / `SOUTH` / `EAST` / `WEST` | One tile. Off-board is a no-op. |
| `PASS` | Nothing. |
| `PICKUP <item> [n]` | Shed to inventory. Any shed item. Seeds never. |
| `DROP` | Whole inventory to shed. Shed-adjacent only. |
| `PLACE <item> [n]` | Animal onto a matching empty structure under the unit, else items into the shed when shed-adjacent. |
| `PLANT <crop>` | Consumes one seed. Empty unlocked tile only. |
| `WATER` | Once per day per plant. |
| `HARVEST` | Takes `yield_units` into inventory. One-time crops vanish. |
| `FERTILIZE` | Consumes 1 fertilizer. Covers today, +1, +2. |
| `BUILD_COOP` / `BUILD_PASTURE` | On an empty tile. |
| `FEED` | Consumes 1 wheat from the unit's inventory. Once per day. |
| `CARE` | Once per day. Banks a yield bonus. |
| `COLLECT_FERTILIZER` | 1 fertilizer from an animal, once per day. |
| `DIG` | Removes a plant, weed, or empty structure. An occupied structure cannot be dug. |

`PLANT` is validated atomically per crop: if the units request more seeds of a crop than the player holds, every `PLANT` of that crop this turn is dropped.

### Market ops

`["BUY_SEED", crop, n]`, `["BUY_PRODUCT", item, n]`, `["BUY_ANIMAL", animal, n]`, `["SELL", item, n]`, `["HIRE"]`, `["BUY_LAND"]`.

Only the first 10 orders are processed. The rest are dropped silently.

## Crops

| Crop | Seed | Base price | First yield | Max yield day | Interval | Max yield | Ongoing |
|:---|---:|---:|---:|---:|---:|---:|:---|
| Wheat | 10 | 25 | day 2 | day 4 | — | 6 (4 unfertilized) | no |
| Carrot | 20 | 35 | day 2 | day 3 | — | 4 (3 unfertilized) | no |
| Tomato | 50 | 60 | day 8 | day 8 | 1 day | 4 productions | yes |
| Strawberry | 100 | 120 | day 10 | day 10 | 2 days | 4 productions | yes |
| Melon | 80 | 250 | day 10 | day 12 | — | 6 | no |

### One-time crops

- A new plant starts at `yield_units = 1`.
- The watering bonus window runs from `ceil(max_yield_day / 2)` to `max_yield_day` inclusive. Watering inside it adds 1 unit, or 2 if the tile is also fertilized, capped at `max_yield`.
- `HARVEST` before `first_yield_day` is a no-op.
- Melon caps at 6 by age 10 with plain water, or age 8 with fertilizer, so ages 11 and 12 add nothing.

### Ongoing crops

- A new plant starts at `yield_units = 0`.
- Production fires at the end of day, at ages `first_yield_day`, then every `interval` days: tomato at 8, 9, 10, 11; strawberry at 10, 12, 14, 16.
- Each production adds 1 unit, or 2 if the plant was both watered that day and fertilized. Capped at `max_yield` held.
- After the fourth production the plant is dead and starts to decay.

### Water, decay, weeds

- Every plant must be watered every day. `consecutive_unwatered` hits 2 at the end-of-day refresh and the tile becomes a weed.
- A fresh plant starts at `consecutive_unwatered = 1`. Water it on the day it is planted or it dies that night. There is no grace period.
- Decay starts at `max_lifespan_step`: one day after `max_yield_day` for one-time crops, one day after the last scheduled production for ongoing crops. From then `yield_units` drops by 1 every other turn until 0, then weed.

## Animals

| Animal | Cost | Structure | Product | Base price | First yield | Interval | Max held |
|:---|---:|:---|:---|---:|---:|---:|---:|
| Goose | 300 | Coop | Egg | 50 | day 4 | 1 day | 4 |
| Cow | 400 | Pasture | Milk | 160 | day 8 | 2 days | 6 |
| Sheep | 500 | Pasture | Wool | 200 | day 6 | 3 days | 6 |

- Buying puts the animal in the shed. A unit must `PICKUP` it, walk to a matching empty structure and `PLACE` it.
- Animals produce forever while fed. `max_held` caps unharvested product on the tile, not lifetime output.
- Feeding costs 1 wheat per animal per day. Two missed days and the animal escapes for good; the structure stays.
- A freshly placed animal starts at `consecutive_unfed = 0`, so it survives its first day unfed.
- `CARE` on a day the animal was also fed banks +1. The whole bank is paid out on the next scheduled production, but only if the animal is fed that day; otherwise the bank is wiped and only the base 1 lands. The steady rate with daily care is `(1 + interval) / interval` per interval.
- Every surviving animal makes 1 fertilizer available at end of day, fed or not. It does not accumulate — an animal left alone for five days still has 1.

## Fertilizer

- Bought at market price (base $100) or collected free from animals.
- `FERTILIZE` covers today and the next two days.
- One-time crops: the bonus per watered day goes from 1 to 2.
- Ongoing crops: scheduled production goes from 1 to 2, but only on days the plant is also watered.
- Nothing in the game consumes fertilizer except `FERTILIZE`, and it can be sold.

## Town demand

- The town centre consumes 1 of every product except fertilizer every 24 turns (once a day), flat all season.
- A new shop unlocks every 3 days, drawn uniformly with replacement, capped at 8 instances. Duplicates are normal. Once unlocked, a shop stays.
- Each shop instance consumes 1 of every product it demands every 4 turns, that is 6 per day. A single-product shop consumes double.

| Shop           | Demands                           |
|:---------------|:----------------------------------|
| Bakery         | egg, wheat                        |
| Pizza Shop     | milk, tomato, wheat               |
| Brunch Spot    | egg, wheat, strawberry            |
| Yarn Store     | wool (2x)                         |
| Ice Cream Shop | strawberry, milk                  |
| Pet Cafe       | carrot (2x)                       |
| Smoothie Shop  | strawberry, milk                  |
| Farmers Market | wheat, carrot, tomato, strawberry |

Town consumption drains market inventory for free, which lifts prices.

## Market

Seeds and animals have fixed prices. Product prices move with market inventory and persist across days.

- Every product starts at `I0 = 10,000`. Price is `base` at `I0`, rises as inventory falls, and falls as inventory rises.
- Only `WHEAT` and `FERTILIZER` can be bought back with `BUY_PRODUCT`. Every product can be sold.
- A sell is quoted at pre-sell inventory; a buy is quoted at post-buy inventory. A buy then a sell of the same item nets exactly zero.
- A unit sold at the $1 floor is paid for but not added to market inventory.
- Selling requires the item in the shed, not in a unit's inventory.

### Order processing

Orders are processed index by index, one unit at a time, with both players in lockstep. At index `i` both players see the same pre-commit inventory, both get that quote for their unit, then both commit, then the price refreshes. Consequences:

- A player's own sells push their own later units down the curve.
- `HIRE` and `BUY_LAND` are atomic and consume an order slot.
- Because only 10 orders survive, the order index matters. A sell at index 0 clears above a sell the opponent placed at index 3.
- If a player runs out of money mid-order, the rest of that order is dropped.

### Price function

```
price(inv) = base + sign * amp * f(|inv - I0|)
  sign = +1 if inv < I0 (scarcity), -1 if inv > I0 (glut)
  amp  = target * base / f(T)
  f in { linear, sq, sqrt, log, log10, hinge }     log is ln(1+x)
  hinge: u = x/T, f = u + 8 * max(0, u-1)^2
```

Floored at $1 and rounded to the dollar. `T` is one 5x5 field's 24-day output at optimal watering without fertilizer.

| Resource | Base | T | Below | Below target | Above | Above target | P(I0-T) | P(I0+T) | P(I0+2T) |
|:---|---:|---:|:---|---:|:---|---:|---:|---:|---:|
| Wheat | 25 | 400 | sqrt | 0.80 | log | 0.20 | 45 | 20 | 19 |
| Carrot | 35 | 450 | hinge | 1.00 | sqrt | 0.70 | 70 | 10 | 1 |
| Tomato | 60 | 200 | hinge | 0.40 | sqrt | 0.60 | 84 | 24 | 9 |
| Strawberry | 120 | 100 | sqrt | 0.70 | linear | 1.60 | 204 | 1 | 1 |
| Melon | 250 | 300 | log | 0.20 | sq | 3.60 | 300 | 1 | 1 |
| Egg | 50 | 332 | hinge | 0.40 | log | 0.20 | 70 | 40 | 39 |
| Milk | 160 | 122 | sqrt | 0.60 | linear | 1.60 | 256 | 1 | 1 |
| Wool | 200 | 105 | log | 0.20 | sq | 3.20 | 240 | 1 | 1 |
| Fertilizer | 100 | 200 | linear | 0.40 | linear | 0.40 | 140 | 60 | 20 |

The four premium goods (strawberry, melon, milk, wool) have `above_target > 1`, so a modest glut sends them straight to the floor. Carrot, tomato and egg use `hinge` below `I0`: calm at ordinary demand, then a steep climb once demand runs past `T`.

`tests/test_price_model.py` and `tests/test_tables.py` pin our copies of these numbers to the simulator.

## Turn order

Per turn:

1. Unit actions for both players, simultaneously.
2. Market queue, index by index, both players in lockstep.
3. Town consumption: shops every 4 turns, centre every 24.
4. Plant decay.
5. If this was the last turn of the day, the end-of-day refresh.

End-of-day refresh, per player:

1. Plants: watering counter, weed check, then ongoing-crop production.
2. Animals: feed counter, escape check, scheduled production plus care bonus, then a new care bonus is banked, then fertilizer becomes available.
3. Weed spawn on empty tiles.
4. Every unit inventory is dumped into the shed; overflow is lost.
5. The farmer respawns at `(4,4)`, hands are cleared, `hires_today` resets.
6. Every 3 days a new shop unlocks.

## What each player can see

Observation:

```py
{
  "player": int, "step": int, "day": int, "hour": int,
  "farms": [farm, farm],
  "market": {"inventory": {...}, "prices": {...}},
  "town":   {"unlocked_shops": [...]},
  "private": {"shed": {...}, "seeds": {...}, "inventories": [...]},
}
```

Public, for both farms: `money`, the full `tiles` grid with every plant and animal field (`planted_day`, `yield_units`, `watered_today`, `consecutive_unwatered`, `fertilized_until_day`, `fed_today`, `consecutive_unfed`, `pending_care_bonus`), `farmer`, `hands`, `unlocked_quadrants`, `hires_today`.

Hidden: only the opponent's `private` block — their shed, their seeds, and the inventories their units carry.

So remaining supply is not guessed. Both farms' tiles plus the fixed crop tables give a hard ceiling on what will still reach the market this season. Only goods already harvested into the opponent's shed are unknown.

## Rules found by experiment

Not in the upstream docs, or not obvious from them. Numbers in [EXPERIMENTS.md](EXPERIMENTS.md).

- Farm hands are close to free. Eight hands cost $54 a day against tens of thousands in revenue, so hire the maximum. Labour is not the constraint; about a third of unit-turns were idle even after livestock landed.
- No explicit `DROP` trip is needed except on the final day, because inventories auto-drop at end of day.
- Melon is worth about 5x carrot per tile-day at base price and no shop demands it, but it floors past roughly 150 units. With both farms dumping melon it sells at an average around $42 against a peak of $272.
- Town demand can outrun supply and lift prices all season, but two farms producing the same crop glut it instead. Carrot falls from $35 to $12 in a mirror match.
- Sells must lead the market order list, steepest curve first, because both players share one curve per index and the tail is truncated.
- Milk and strawberry carry the business.
