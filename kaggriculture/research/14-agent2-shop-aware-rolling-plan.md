# Agent 2.0 shop-aware rolling plan

Status: execution plan for standalone experiment 39.14.

## Objective

Add an information-safe town-demand forecast and rolling planning window. The
model uses the exact open shops, exact town timing and uncertainty about future
shop types. It does not change the live agent in this experiment.

Source predecessor: `ea73017`. Frozen score comparator remains 1.14.0 commit
`b74a3ea`, SHA-256
`86951703eac27253938500eac664650c1e927d1b86b26ed84be008f24739d699`.

## Information boundary

The observation exposes only `town.unlocked_shops`. The episode seed is removed
from configuration and stored outside agent observations. Future shop types are
therefore hidden during a live match, although a completed replay contains the
resolved sequence.

The production forecast must not read the replay or simulator seed. It branches
uniformly over every possible type at the next opening. Shops after that branch
use expected demand. A separate offline oracle can later use a resolved replay,
but its output cannot enter an agent action.

## Time model

The model uses source steps. Market orders run before town consumption. A shop
opens at the end of the last step before day 3, 6, 9 and later multiples of 3.
The new shop first appears in observation and first consumes after market orders
at hour 0 of its opening day. Shops consume every 4 steps. The town centre
consumes every 24 steps. At most 8 shop instances open and duplicates are legal.

The rolling result has three horizons:

1. action horizon: the rest of the current day;
2. stable strategy horizon: through the step before the next shop opening;
3. investment horizon: through the terminal step.

The coordinator recalculates each day and immediately after a changed shop
signature. Crop, animal and land investments still use the full remaining game,
so the three-day boundary does not discard their later income.

## Forecast

Each next-shop branch stores its probability and per-step town drain from the
current source step through the terminal step. Current shops and town-centre
events are exact. The next shop type is exact inside its branch. Later unknown
shops contribute expected fractional demand. The probability-weighted result
provides the inventory correction consumed by crop and animal MILP inputs.

If all 8 shops are already open, the result has one deterministic branch and no
shop replan boundary.

## Validation

Tests cover observation secrecy, opening boundaries, order-before-consumption,
duplicates, single-product double demand, cap 8, terminal boundaries, branch
probabilities, expected-demand conservation and rolling replan triggers.

The registered runner records inputs, exact and uncertain events, horizons,
probability sums, expected drain and source hashes. Full tests, Ruff and an
adversarial review gate the result.

## Integration boundary

This experiment emits forecast data only. A later coordinator integration will
translate expected inventory correction into daily crop and animal base-market
paths and compare complete 30-day games with frozen 1.14.0.

## Unresolved questions

- Risk objective for low-probability high-value shops.
- Opponent order forecast beyond the accepted no-future-orders scenario.
