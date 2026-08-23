# Agent 2.0 economic program

Status: canonical roadmap. Stage A0 is the next executable plan. Every later
stage requires its own reviewed execution plan before code starts.

## Scope decision

Write the economic policy from scratch. Reuse 1.14.0 only for:

- verified game constants and price functions;
- legal task generation and action preconditions;
- task execution, routing, safety and endgame behavior;
- market-order execution and opponent-stock memory;
- exact fallback and the frozen score comparator.

Do not reuse `_crop_value()`, `_dynamic_plan()` or fixed
`COW:7,SHEEP:5` as economic decisions. The first controlled crop stage may call
the frozen `_dynamic_plan()` only to preserve its animal locations before
replacing crop targets.

The game has 30 days: `0..29`. `T=30` is a mathematical terminal sentinel, not
an observed simulator day. The last market-clearing action is day 29 hour 22.

Frozen comparator:

- commit `b74a3ea`;
- `agents_1.0.x/v1_14_0_central_herd.py`;
- SHA-256
  `86951703eac27253938500eac664650c1e927d1b86b26ed84be008f24739d699`.

Accepted Agent 2.0 predecessor: commit `87f5d30`.

## Architecture

Agent 2.0 has two independent economic phases:

```text
observation
-> configured strategy.prepare(world), if present
-> frozen executor with optional validated strategy
-> market.plan(world, frozen_orders)
-> final action and market-memory synchronization
```

The accepted `economy_factory` and
`FrozenEconomyPlanner.plan(world, frozen_orders)` remain the post-decision
market phase. A new `strategy_factory` owns the pre-decision strategy phase.

With `strategy_factory=None`, the policy creates no strategy planner. It calls
the exact accepted coordinator path without wrappers, state extraction or
strategic computation.

Each policy owns one baseline and one market planner. A configured policy also
owns one strategy planner. Duplicate observations call neither planner. Episode
reset reloads baseline and resets each configured planner. No module-level
mutable strategy state is allowed.

## Stage A0: neutral crop-strategy seam

Stage A0 changes structure only. It cannot change a crop, herd target, order or
unit action in its default configuration.

### Files

Stage A0 changes at most six files:

1. `agents_2.0.x/round37_1_task_graph/agent_2/strategy.py`
2. `agents_2.0.x/round37_1_task_graph/agent_2/coordinator.py`
3. `agents_2.0.x/round37_1_task_graph/agent_2/baseline.py`
4. `agents_2.0.x/round37_1_task_graph/agent_2/policy.py`
5. `agents_2.0.x/round37_1_task_graph/agent_2/adapter.py`
6. `tests/test_agent_2_strategy_seam.py`

Existing coordinator, task, shell and package tests remain unchanged and must
pass. Result JSON and `EXPERIMENTS.md` are a separate documentation commit after
the implementation gate.

### Contracts

`strategy.py` defines:

```text
CropStrategy(
    targets: tuple[(x, y, crop_or_none), ...],
)
```

`targets` contains only crop overrides for currently empty, unlocked tiles.
Coordinates are unique. Crop values are one of the five game crops or `None`.
`None` removes the frozen crop target from that eligible tile. The type stores
no dictionaries or lists and contains at most one target per board tile.

```text
StrategyPlanner.prepare(world) -> CropStrategy | None
StrategyPlanner.reset() -> None
FrozenStrategyPlanner.reset() -> None
FrozenStrategyPlanner.prepare(world) -> None
```

`Agent2Policy` and `create_agent()` add `strategy_factory=None` after the
existing arguments. Existing positional and keyword callers remain valid.

`Agent2Coordinator` becomes
`Agent2Coordinator(baseline, economy, strategy=None)`. Existing two-argument
construction remains valid.

`Agent2Coordinator` owns `strategy` and preserves the existing market planner:

```text
strategy = safe_prepare(world)
decision = baseline.decide(obs, strategy)
final_orders = existing_market_phase(world, decision.action.get("market", ()))
```

Only a configured planner calls `prepare`. Before baseline starts, the
coordinator parses `world.data` and validates the complete result. It rejects
non-exact integer coordinates including `bool`, out-of-board coordinates,
duplicates, unknown crops, excess targets, occupied tiles and locked tiles. A
strategy exception, invalid type or invalid target resets only the strategy
planner and becomes `None`. It does not reload or run baseline during
validation.

`BaselinePolicy.decide(obs, strategy=None)` preserves its current one-argument
behavior. For a validated crop strategy it:

1. saves the exact `_dynamic_plan` callable;
2. installs one closure;
3. calls frozen `agent(obs)` exactly once;
4. restores the exact callable identity in `finally`;
5. never retries after frozen execution starts.

The closure calls the original `_dynamic_plan()` once. It copies its result,
preserves all frozen animal targets and applies only validated crop targets to
positions whose frozen target is not an animal. The closure performs no parsing,
I/O, allocation search or mutable state lookup.

The closure accepts the exact frozen signature
`(tiles, day, inventory, shops, board_size=10, budget=None, seeds=None)` and
forwards all seven values. Coordinates use `(x, y)` while tile access uses
`tiles[y][x]`. A target that collides with any frozen animal target is ignored;
the frozen animal target wins. With `KAGG_PLANNER=fixed`, the wrapper is not
called and the strategy has no behavioral effect.

Stage A0 does not patch `_parse_herd()`, `_desired_herd()`, market functions,
land, hiring or feed functions.

### Failure behavior

- `strategy=None`: exact current path, no wrapper.
- prepare or validation failure: reset only strategy and use `None`.
- frozen executor failure: restore wrapper and propagate; do not retry.
- market planner failure: retain the accepted frozen-market fallback.
- synchronization failure: retain the strategy-adjusted baseline action and its
  original market orders; do not call baseline again.

### Stage A0 tests

- default strategy is action-identical to commit `87f5d30` and frozen 1.14.0;
- existing market-only injection still changes only `action["market"]`;
- crop strategy changes only eligible crop targets;
- frozen animal targets cannot be overwritten;
- duplicate does not call strategy;
- episode reset resets strategy and reloads baseline;
- prepare failure resets strategy without reloading baseline;
- two seats own different strategy instances;
- invalid coordinates, duplicate coordinates, locked tiles, occupied tiles and
  unknown crops fall back before baseline;
- `None` removes only an eligible crop target;
- `(x, y)` is not transposed and `bool` is not accepted as an integer;
- central, dairy and non-central frozen animal targets always win collisions;
- fixed planner mode remains action-identical and ignores crop strategy;
- `_dynamic_plan` identity is restored after success and baseline exception;
- frozen `agent()` is called once per nonduplicate observation;
- observation and strategy inputs are not mutated; default-path baseline action
  and task graph remain unchanged;
- packed artifact works without new dependencies.

### Stage A0 gates

- full tests and Ruff pass;
- 40 registered seeds, both seats, zero action, observation, reward, status and
  failure mismatches against frozen 1.14.0;
- summed CPU ratio at most `1.25x`;
- default-seam CPU ratio at most `1.02x` against commit `87f5d30` after
  division by a same-run A/A ordering control; keep both raw measurements;
- cached/default p99 overhead at most 2 ms;
- worst call below 750 ms;
- signed conventional implementation commit;
- separate signed experiment-result commit.

## Stage A1: offline exact economic ledger

Stage A1 does not run inside the agent and cannot change actions. It builds an
hour-aware economic transition model and validates it against the real
environment.

Implement and gate it in this order:

1. `A1a`: cash, order-index market, town demand and price transitions;
2. `A1b`: shed, seed, unit and pending-purchase inventories and delays;
3. `A1c`: crop planting, age, water, fertilizer, harvest and overflow;
4. `A1d`: animals, feed, care, production and full differential replay.

Each substage must pass its own boundary fixtures before the next substage. The
ledger can be exact. Future work capacity and travel remain calibrated model
inputs and must never be reported as exact.

The ledger keeps separate balances for:

- seeds;
- shed goods;
- every unit inventory;
- pending market purchases;
- standing crop yield;
- animal-held product and fertilizer;
- cash before every market order index;
- market inventory before every unit of each order.

The state also stores absolute `step`. Town ticks use `step % 4` and
`step % 24`; day and hour alone are not sufficient.

The transition order matches `RULES.md`:

1. simultaneous unit actions;
2. ten market-order indexes with both players in lockstep;
3. town demand;
4. decay;
5. end-of-day refresh when applicable.

Revenue for selling `q` units starts at the pre-sell quote:

```text
revenue(item, inventory, q)
    = sum_(k=0..q-1) price(item, inventory + accepted_units_before_k)
```

A unit paid at the $1 floor does not enter market inventory. A product is
sellable only from the shed. Purchases, land and hires become available on the
next observation, not the next day.

Boundary fixtures cover every crop age, every animal production/feed/care
boundary, order exhaustion, atomic planting, shed overflow, day 28 refresh,
day 29 hours 21, 22 and 23, carried goods, final drop and terminal inventory.
Also cover day 0 hour 0, both town-tick periods, shop unlocks, market parameter
overrides, both players at the same order index, post-buy prices, seed storage
outside the shed limit and the terminal transition after the final action.

Build a dedicated state-injection harness or controlled trajectories for these
comparisons. Compare the ledger with the real simulator and exhaustive small
fixtures before using it to rank a policy.

## Stage A2: whole-farm oracle and ranking

Build counterfactual macro portfolios from matched states. Use simulator
rollouts or exhaustive fixtures to measure ranking; one realized replay path is
not sufficient.

The first implementation is a correctness-first research oracle. It may solve
slowly and is not required to fit the Kaggle runtime budget. Its purpose is to
measure the best score that this economic method can realize against frozen
1.14.0. Record solver wall time, optimality gap, timeout and model size for
every decision. Do not weaken the model only to make the first comparison fast.

Use `scipy.optimize.milp` only in a tool outside `agent_2/`. The runtime package
must not import SciPy. Define discrete quantity variables and explicit
piecewise price, floor and lockstep-order constraints in the separate A2
execution plan.

The mathematical state horizon covers the current hour through day 29 hour 22.
The objective is realized terminal cash. Goods unreachable by the final sale
have zero value.

The first model includes only:

- crop counts and start days;
- seed purchases;
- exact existing standing crops;
- fixed 1.14.0 herd, land, hiring and routing capacity;
- hourly cash and inventory feasibility;
- marginal market revenue under one registered frozen opponent-order model;
- calibrated action and travel capacity.

`A2a` ranks portfolios in shadow mode and does not control actions. `A2b` uses
simulator rollouts through the A0 seam to measure the realized score of the
selected portfolio while still outside the submitted agent. Stage B is the
first live controlled arm.

For every A2 run, freeze one opponent future-action assumption: recorded order
replay, no future orders or a named scenario from Stage G. Do not call revenue
exact unless the opponent path is fixed.

No live-agent CPU claim is required for A2. Required calibration outputs:

- mean absolute terminal-cash error by day;
- Spearman rank correlation over candidate portfolios;
- top-choice regret against simulator rollout;
- infeasible-plan rate;
- planned-versus-executed crop counts;
- error by market and shop regime.

Calibration thresholds and sample sizes must be registered before the run. The
model cannot enter A2b until it ranks counterfactuals better than frozen
`_dynamic_plan()` on held-out states. Report ledger-transition error separately
from capacity, route and portfolio-ranking error.

## Kaggle runtime path

Do not put SciPy or a general MILP solver in the submitted agent during the
research stages. After the research oracle wins on realized score, measure these
deployment forms in separate experiments:

1. enumerate a small registered portfolio set without MILP;
2. solve once at episode start and after a material event;
3. solve once per day with a warm start and a strict time limit;
4. distill oracle decisions into tables, thresholds or a small deterministic
   policy.

A material event is a failed purchase, new land, unexpected weed, animal loss,
opponent stock shock or a plan-feasibility change. Do not re-solve after a
normal one-tile move. Kaggle deployment starts only after one form passes the
score gates and the existing live-agent CPU gate.

## Controlled experiments

Every controlled arm gets a new worktree, exact frozen predecessor, flag,
registered seeds, result JSON, negative-result record and signed commits. Test
each mechanism alone against frozen 1.14.0 before testing it on an accepted
stack.

Dependency DAG:

```text
A0 -> A1a -> A1b -> A1c -> A1d -> A2a -> A2b -> B
A1d -> C
A1d -> D
B,D -> E
A1d -> F
B,C,D,F -> G
confirmed B,C,D,E,F,G arms -> H -> 2.0 release gate
```

Every B-G standalone arm starts from the same accepted coordinator predecessor
and enables only its declared seam. H combines only separately confirmed arms.

### B: crop portfolio

- inject crop targets only through Stage A0;
- keep herd, land, hiring, market and routing algorithms frozen; allow only
  their causal reaction to changed crop targets;
- first run the slow A2 oracle-driven choice against frozen 1.14.0 without a
  Kaggle runtime limit;
- then test no melon, strawberry-heavy and labor-priced arms
  separately;
- include existing standing harvest and final-sale reachability.

### C: feed make-or-buy

- add a separate reviewed feed seam;
- keep crop and herd targets frozen for standalone tests;
- compare growing wheat with buying wheat using seed, tile, action, travel,
  market, slot and latency costs;
- test zero-, one- and two-day secured reserve separately;
- track hungry animal-days, bought wheat, grown wheat and terminal wheat.

### D: animal portfolio

- add a separate reviewed herd seam;
- include purchase, shed arrival, pickup, structure build, placement, feed,
  care, harvest, fertilizer and sale;
- compare no purchase and one marginal goose, cow or sheep before larger
  portfolios;
- do not reuse fixed herd value as the economic score.

### E: central option layout

- keep accepted economic counts fixed;
- existing occupied structures never move;
- compare frozen central placement, flow-distance placement and a temporary
  one-time crop ending before animal placement;
- ongoing temporary crops require explicit dig cost and cannot block placement.

### F: land and hiring

- test land and hiring independently;
- model their next-observation availability and actual partial-day capacity;
- include order slots, cash order and nonlinear hire cost;
- do not infer value from theoretical capacity that the executor does not use.

### G: opponent best response

- start with current public standing production;
- then add registered strawberry-heavy, wheat-livestock, melon-heavy, mixed and
  disruption scenarios from held-out replays;
- test expected money, conservative money and opponent-relative money as
  separate objectives;
- do not implement a full Nash solver before scenario best response wins.

### H: combination

- combine only independently confirmed arms;
- add one arm at a time by confirmed effect size;
- rerun fresh confirmation and regression after every addition;
- reject an interaction that removes an earlier gain.

## Economic equations

The whole-farm model maximizes realized terminal cash:

```text
cash[t+1] = cash[t]
            + accepted_sales[t]
            - accepted_seed_cost[t]
            - accepted_animal_cost[t]
            - accepted_feed_cost[t]
            - accepted_land_cost[t]
            - accepted_hire_cost[t]
```

Feed is a feasibility balance, not a hard preference for home production:

```text
secured_wheat_before_feed[t]
    >= feed_due[t] + reserve_target[t]
```

The purchase value is counterfactual:

```text
delta_value(purchase)
    = best_terminal_cash(with purchase)
      - best_terminal_cash(without purchase)
```

The crop model includes seed, exact yield schedule, sale reachability, occupied
tile time, actions, travel and the effect of our quantity on later prices.

The layout model uses service flow times distance, but measured executor
congestion replaces theoretical distance when available.

The opponent scenario stage uses:

```text
expected_value(plan)
    = sum_s probability[s] * terminal_cash(plan, s)
      - risk_weight * downside(plan, s)
```

## Score gates

Screen each arm on 40 paired seeds in both seats. Advance only when paired mean
money and match points are both positive. Confirm on 100 fresh paired seeds.
Confirmation requires:

- clustered 95% lower bound above zero for paired money;
- clustered 95% lower bound above 50% for match points;
- zero failures and timeouts;
- live-agent CPU ratio at most `1.25x`;
- no increase in unreachable terminal inventory;
- no regression in a varied opponent pool.

The varied opponent pool uses registered equal seed and seat weight. Its
clustered 95% lower bounds must remain above zero for paired money and above
50% for match points.

Before a 2.0 release, rerun the accepted combination on at least 400 fresh
paired seeds in both seats against frozen 1.14.0 and the varied opponent pool.
The same lower-bound requirements apply. The live candidate must remain at most
`1.25x` frozen 1.14.0 CPU. A neutral seam must also remain at most `1.02x` its
direct accepted predecessor.

Run direct head-to-head games against 1.14.0 and paired games against fixed
1.13.0. Record exact commands, hashes, environment version, seed ranges,
calibration data, runtime and all negative results.

## Stop conditions

- Stop when a stage changes behavior outside its declared boundary.
- Stop when the model and real simulator disagree on a boundary transition.
- Stop when the frozen executor realizes fewer than the registered fraction of
  planned macro decisions.
- Stop an arm that improves predicted value but loses realized terminal cash.
- Do not merge Agent 2.0 economic code into root `main.py` before the combined
  candidate passes confirmation and regression.

## Unresolved questions

- Stage A2 calibration sample size and minimum rank correlation.
- Initial action and travel capacity calibration method.
- Opponent-scenario probability estimator.

The capacity calibration blocks A2. It does not block A0 or A1.
