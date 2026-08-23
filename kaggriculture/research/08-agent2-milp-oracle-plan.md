# Agent 2.0 A2a crop MILP oracle plan

Status: execution plan for the A2a oracle core. Ranking calibration remains a
separate gate before A2a can control an A2b rollout.

## Objective

Build a correctness-first, offline, whole-horizon MILP that chooses crop seed
purchases, planting cohorts, harvest timing, wheat purchases and crop sales
from a matched state through day 29 hour 22. Its objective is realized terminal
cash under one registered exogenous market scenario. Goods that cannot reach a
legal sale have zero objective value.

Source predecessor: `0266299`. Frozen score comparator remains 1.14.0 commit
`b74a3ea`, SHA-256
`86951703eac27253938500eac664650c1e927d1b86b26ed84be008f24739d699`.

A2a is shadow-only. It does not change a live agent action and makes no realized
score claim. A2b will execute its first-day choice in the simulator.

## Files

The implementation commit changes only:

1. `tools/economics/milp_oracle.py`;
2. `tools/economics/run_milp_oracle.py`;
3. `tests/test_economic_milp_oracle.py`.

The result commit adds `research/round39_7_milp_oracle_seed_3980000.json` and
updates `EXPERIMENTS.md`.

## Runtime and boundary

`milp_oracle.py` is a research tool. It imports SciPy and accepted A1a-A1d
constants but is never imported by `agent_2/` or the submission artifact. The
first implementation can run longer than the Kaggle per-call limit.

The model uses daily crop cohorts and exact terminal source-step feasibility.
It does not claim exact route capacity or exact future prices. Those are named,
registered scenario inputs. Exact A1a-A1d ledgers remain the later rollout and
calibration authority.

## Input contract

Frozen `OracleInput` stores:

- current source step and final processed source step 718;
- current cash and protected cash reserve;
- existing seeds and sellable goods for every crop;
- every existing standing plant with its crop, planted day, current yield,
  water state, fertilizer horizon and board position;
- tile capacity for each remaining day after fixed 1.14.0 animal and land use;
- crop-action capacity for each remaining day after fixed animal work;
- fixed daily wheat demand and fixed daily net cash flow;
- fixed base market inventory for each crop and day;
- marginal seed, bought-wheat and crop-sale prices;
- remaining daily market-order slots;
- terminal return actions charged to every harvested tile;
- named opponent and market scenario.

All vectors cover `current_day..29` and use immutable tuples. Validation rejects
wrong lengths, unknown scenarios, negative balances, nonfinite values, terminal
steps outside `0..718`, negative capacities and a reserve above current cash.

The registered first run uses scenario `no-future-opponent-orders-v1`. Its base
inventory path is supplied explicitly. It is not reported as an exact forecast.
The frozen herd, land, hiring and route effects enter only through registered
tile, action, wheat, order-slot and cash-flow vectors.

## Crop cohort options

Generate integer cohort options for every crop and legal planting day.

For one-time crops, create one option for every harvest age from
`first_yield_day` through `max_yield_day`. Yield starts at one and adds one for
each unfertilized watering in the exact A1c yield window, capped by
`max_yield`. The option consumes one plant action, one daily survival watering,
one harvest action and the registered return actions.

For tomato and strawberry, create one option for every legal final production
included in the harvest. Production days come from accepted A1c tables, add one
unit each without fertilizer and stop at four. The crop occupies its tile
through the terminal day because A2a does not add a post-harvest dig action.

An option is legal only when:

```text
plant source step
< harvest source step
+ harvest action
+ terminal return actions
<= 718
```

The daily model assumes planting at the first available action of its day and
harvest at hour 0 of its harvest day. `OracleInput.first_plant_day` accounts for
a late current observation. Options with no mature, transported and sellable
yield are not created.

Existing plants create harvest alternatives without seed cost or plant action.
Each exact standing plant selects at most one future alternative or is
abandoned. Its current held yield, remaining production schedule, water work,
fertilizer horizon and terminal return feasibility come from accepted A1c.
Existing plants continue to consume one tile, but they do not consume the
capacity reserved for currently empty tiles.

## Variables and constraints

Use `scipy.optimize.milp` with integral variables and sparse linear
constraints.

Variables:

- integer cohort count for every generated crop option;
- binary harvest alternative for every existing standing plant;
- integer seed purchases by crop and day;
- binary seed-order activation by crop and day;
- integer bought wheat by day;
- binary wheat-order activation by day;
- binary marginal crop-sale units by crop, day and registered unit index;
- binary crop-sale order activation by crop and day;
- integer end-of-day seed balances by crop and day;
- integer end-of-day sellable-goods balances by crop and day.

Constraints:

1. seed balance includes current seeds, same-day buys and same-day planting;
2. crop goods balance includes current goods, cohort harvest, wheat feed, wheat
   buys and sales;
3. active cohort occupancy never exceeds the registered tile capacity;
4. plant, water, harvest and return work never exceeds daily crop capacity;
5. cumulative cash never drops below the protected reserve after fixed cash
   flow, seeds, wheat and crop revenue;
6. seed, wheat and sale quantities activate their market-order binaries;
7. daily active order binaries never exceed registered market slots;
8. marginal sale variables cannot exceed available goods;
9. all integer balances and quantities remain nonnegative;
10. only generated terminal-feasible cohorts can be selected.

For each crop and day, zero-based marginal sale coefficient `k` is the accepted
A1a quote at `base_inventory[crop, day] + k`. Marginal coefficients are
nonincreasing.
Maximization therefore selects the correct highest-price prefix without an
extra precedence constraint. Tests assert this property for every registered
curve. The exogenous daily base path intentionally does not include feedback
from counterfactual sales on later days; result metadata reports this as a
market-scenario approximation.

The minimization objective is negative terminal cash change. Fixed cash flows
are constants. Unsold terminal goods have coefficient zero. The result reports
terminal cash, incremental crop profit, remaining goods and seeds, not a
simulator score.

## Result contract

Frozen `OracleResult` contains:

- solver status, message, success flag and optimality gap;
- wall time, variable count and constraint count;
- objective terminal cash and incremental crop profit;
- selected crop cohorts, seed buys, wheat buys and daily crop sales;
- end-of-day cash, seeds and goods;
- terminal unsold goods;
- scenario name, input hash and model hash.

No result is called optimal unless SciPy reports success. Timeout and infeasible
results keep their status and have no fabricated plan.

`run_milp_oracle.py` registers one seed-3,980,000 initial-state scenario,
solves it once, writes canonical JSON and prints a short decision table. The
script accepts a solver time limit and relative MIP gap. The stored run uses a
120-second limit and zero requested gap.

This single run validates the oracle core only. It does not satisfy the
canonical A2a ranking gate. A later registered calibration must measure rank
correlation, regret, infeasible-plan rate and planned-versus-executed counts on
held-out matched states before A2b can use the plan.

## Tests and gates

Tests cover:

- all crop option maturity, yield, occupancy and action vectors;
- no melon or strawberry option when planted too late;
- terminal route actions excluding an otherwise mature crop;
- same-day seed purchase and planting balance;
- tile turnover for one-time crops and terminal occupancy for ongoing crops;
- action-capacity rejection;
- cash reserve and same-day revenue reinvestment;
- wheat production, purchase and fixed-feed balance;
- market-order activation and slot limits;
- decreasing marginal sale prices and exact A1a quotes;
- zero value for unsold terminal goods;
- one small hand-computed optimum;
- infeasible and time-limited status handling;
- deterministic canonical input and output.

Gates:

- reviewed plan commit before implementation;
- all tests and Ruff for changed files pass;
- registered run completes within 120 seconds;
- solver success with requested relative gap zero;
- every selected cohort satisfies terminal maturity and return feasibility;
- recomputed balances match the reported plan exactly;
- adversarial review has no surviving issue;
- signed conventional implementation commit;
- separate signed result commit.

## Non-goals

- live action control or realized score comparison;
- choosing animals, land or hires inside the MILP;
- exact future opponent actions or exact future prices;
- cell-level layout or unit route construction;
- Kaggle runtime optimization.

## Unresolved questions

None.
