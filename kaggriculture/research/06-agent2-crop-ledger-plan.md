# Agent 2.0 A1c crop-ledger plan

Status: execution plan for A1c only.

## Objective

Extend accepted A1b with exact crop, seed, land-unlock, decay and crop-refresh
transitions. Prove the last biologically harvestable planting day against
simulator 1.32.7. Keep sale reachability, animal biology, route choice and MILP
decisions outside this stage.

Source predecessor: `bdc244a`. A score comparator is not applicable because
A1c cannot change a live-agent action.

## Files

The implementation commit changes only:

1. `tools/economics/crop_ledger.py`;
2. `tools/economics/validate_crop_ledger.py`;
3. `tests/test_economic_crop_ledger.py`.

The result commit adds
`research/round39_5_crop_ledger_validation.json` and updates `EXPERIMENTS.md`.

## State and composition boundary

`crop_ledger.py` imports only the standard library, accepted
`inventory_ledger.py` and accepted `market_ledger.py`. It does not import
Kaggle, 1.14.0 or Agent 2.0.

It exports frozen:

- `CropSpec` for the five tables copied from simulator `CROPS` and pinned field
  by field by the validator;
- `PlantState` with crop, planted day, watering counters, standing yield,
  lifespan step and fertilizer horizon;
- `CropBoard`, a fixed 10x10 row-major board;
- `CropState`, containing accepted `InventoryState` and both boards;
- `CropEvent` with player, unit, position, operation, crop, quantities and
  failure reason;
- `CropTransition` with `after_units`, `after_town`, `after_decay` and optional
  `after_refresh`.

Board cells are exactly one of:

- `None` for empty unlocked land;
- `"LOCKED"`;
- `"WEED"`;
- `"STRUCTURE"` and `"ANIMAL"` as distinct delegated A1d cells;
- `PlantState`.

`CropState` does not own unit movement. `unit_positions` is an exact external
two-player tuple with one in-board `(x, y)` position for the farmer and every
existing hand at the start of that unit action. A1c exactness is conditional on
these positions. Multiple units can reference the same cell and observe prior
unit mutations in simulator order.

A1c derives A1b shed adjacency from each supplied position with the exact four
board-size-10 access cells. The validator compares the derived value with
simulator `_is_shed_adjacent()`. A separate exact `animal_place_priority` fact
is supplied per executed unit. A true fact delegates that action before A1b
shed `PLACE`; A1d later computes and executes it from richer structure state.
Standalone A1c differential cases require this fact to be false.
`animal_place_priority` is an exact two-player tuple with one exact boolean for
every existing unit.

## Shared unit dispatcher

`apply_crop_phase(
state, unit_actions, unit_positions, animal_place_priority, market_queues,
trace=False
)` uses one unit loop:

1. compute player 0 atomic blocked `PLANT` crops, then process player 0 farmer
   and listed existing hands;
2. compute player 1 atomic blocked `PLANT` crops after player 0 mutations, then
   process player 1 farmer and listed existing hands;
3. for each unit, delegate a true `animal_place_priority` fact;
4. otherwise call accepted A1b `apply_unit_transfer()`;
5. if A1b declines the action, apply the A1c crop operation;
6. freeze `after_units`;
7. call accepted A1a market and town phase with exactly
   `after_units.inventory.market`;
8. append inventories for accepted hires;
9. unlock newly bought land in NE, SW, SE order;
10. freeze `after_town` without advancing the source step;
11. apply plant decay for the source step;
12. on source steps where `(step + 1) % 24 == 0`, apply crop refresh and stop
    before animal refresh, random weeds and accepted A1b inventory day end.

Raw hand behavior matches the simulator: a non-list becomes empty, missing
actions are no-ops and excess hand actions are not executed. Excess list
entries still participate in atomic `PLANT` demand because the simulator counts
them before it ignores their unit execution.

Accepted A1b transfer and A1a market results must be identical to direct calls
for the same phase. A separate integration fixture composes accepted A1b day
end after crop refresh. Accepted `HIRE` and `BUY_LAND` effects are derived from
state differences, never trace.

The final shared A1d dispatcher order is animal-placement priority, A1b shed
transfer, A1c crop operation, then remaining A1d animal operations. A1c tests
pin delegation so A1d does not need to change this order.

## Crop operations

A1c owns only:

- `PLANT crop` on empty unlocked land, consuming one seed;
- `WATER` once per plant per day, including immediate one-time-crop yield;
- crop `HARVEST`, moving all standing yield to the ordered unit inventory and
  removing one-time crops;
- plant `FERTILIZE`, consuming one carried fertilizer and extending coverage
  through current day plus two;
- `DIG` on plants or weeds.

A1c declines animal harvest and structure or animal dig so A1d can handle them
in the same future dispatcher. Differential crop cases do not claim full-board
equivalence for delegated actions. Movement, building, animal placement, feed,
care and fertilizer collection remain outside A1c.

Parser order and exception type match the simulator. Atomic parsing happens
immediately before each player's units, not for both players up front. Commands
are not mutated.
Blocked atomic planting replaces every requested plant of that crop with a
no-op, including otherwise legal existing units.

The crop event records player, unit index, position, operation, crop, yield or
resource quantity before and after, accepted status and failure reason. With
`trace=False`, state and phase composition remain identical and event tuples
are empty.

## Decay and daily refresh

`apply_crop_decay(state, trace=False)` applies source-step decay after town and
before daily refresh. Yield drops by one on each matching parity step from
`max_lifespan_step`. A plant becomes a weed at zero.

`apply_crop_refresh(state, trace=False)` uses current day and produces the next
day state without advancing the stored source step:

- watered plants reset `consecutive_unwatered`;
- other plants increment it and become weeds at two;
- ongoing crops produce on their exact age and interval schedule;
- production adds two only when watered and fertilized that day;
- held yield is capped at four;
- the fourth production sets the exact decay start step.

Random weed spawn is not modeled in A1c. Validation sets `weedSpawnChance=0`.
A1d full replay will add supplied deterministic weed outcomes without changing
crop equations.

Source step 23 applies crop refresh. The later composed end-of-day pipeline
then applies animals, weeds and inventory cleanup. Source step 718 applies
decay but no refresh or inventory dump. Step 719 has no processed action.

## Land and terminal contract

An accepted `BUY_LAND` unlocks the next quadrant after unit actions and before
the next observation. Existing `"LOCKED"` cells in that quadrant become empty;
other cells are preserved. A current-turn purchase cannot make a current-turn
`PLANT` legal.

The model exposes pure helpers with exact crop-name and integer-day validation
for:

- first harvest day;
- scheduled ongoing production days;
- last plant day with at least one mature harvest by a supplied terminal action
  day, default day 29.

These helpers use day indices, not source steps. They prove biology only.
Harvest, drop and sale feasibility remains an A2 work-and-route constraint.
Source step 718 is the final processed action step and maps to day 29.

## Differential validator

`validate_crop_ledger.py` reuses the A1a injector and A1b ordered-inventory
projection. It injects exact 10x10 boards, positions, seeds and raw actions into
a fresh verified simulator snapshot for every case.

For each case it compares:

1. atomic planting and the shared unit phase;
2. the A1a market and town result;
3. accepted hire inventories and land unlocks;
4. simulator `_decay_plants()`;
5. simulator `_daily_refresh_plants()` on end-of-day cases;
6. separately compose accepted A1b inventory day end and compare full simulator
   `_end_of_day()` with empty animal state, `weedSpawnChance=0` and controlled
   shop unlock;
7. ordered inventories, seeds, all board cells and A1a fields after each phase.

Expected model and simulator exceptions must have the same type, phase and
partial state. The validator invokes the same pure per-player dispatcher used
by the public model so a player-1 parse failure can compare the already-applied
player-0 state. Matched exceptions are counted separately. Unexpected
exceptions are failures.
Item-balance invariants cover seeds, standing yield, unit inventory, shed,
harvest and inventory overflow. Direct A1a and A1b composition must match.

Boundary cases cover:

- all five crops at every meaningful age from planting through final decay;
- all exact one-time watering windows, caps and fertilizer orderings;
- every ongoing production boundary, missed water and held-yield cap;
- harvest before and at first yield, repeated harvest and one-time removal;
- atomic planting with exact, insufficient and excess-hand seed demand;
- same-cell unit order for plant-water, fertilize-water, harvest and dig;
- `DIG→PLANT`, `PLANT→DIG`, `WATER→HARVEST`, `HARVEST→WATER`, repeated
  `FERTILIZE` and `PLANT→FERTILIZE` on a shared cell;
- harvest on a decay step, harvest before decay, water before one-time decay,
  final decay before refresh and planting-day survival refresh;
- empty, weed, locked, structure, animal and plant cells, with delegated
  actions tested separately from exact crop transitions;
- missing, malformed, unknown and unhashable raw actions;
- current-turn and next-turn `BUY_SEED` availability;
- every accepted `BUY_LAND` quadrant and current-turn planting delay;
- source steps `0`, `23`, `24`, every decay parity boundary, `717` and `718`;
- latest biological planting day for every crop and no-maturity terminal cases.

Run exactly 5,000 deterministic stratified invariant-valid cases with IDs
`R00000..R04999`, generator schema `crop-v1` and RNG seed `3,970,000`. The
strata cover crop, operation, player, farmer or hand, cell kind, water state,
fertilizer state, source-step phase and land count. Every 100-case smoke prefix
covers all crops, operations, players, unit seats, cell kinds and terminal
phases. Canonical typed input JSON includes schema, seed and every case and
produces `input_sha256`.

The generator emits a coverage manifest with exact counts for every declared
dimension. Tests assert the counts and required cross-products before any
differential comparison, so duplicate or missing strata fail the gate.

The result JSON contains counts, compared fields, matched exceptions, first
mismatch with trace, unexpected failures, elapsed time, environment version,
generator schema, input hash and model, predecessor and simulator hashes.

## Gates

- reviewed plan commit before implementation;
- boundary and 100-case smoke pass before the full run and stop on first error;
- all tests pass;
- Ruff passes for changed files;
- zero field mismatches and unexpected failures for boundary and 5,000 cases;
- two 100-case smoke runs match after elapsed time is removed;
- full validation, including environment construction, finishes within 120
  seconds;
- model imports no simulator, Agent 2.0 or 1.14.0;
- adversarial review has no surviving issue;
- signed conventional implementation commit;
- separate signed result commit.

The normal test suite runs boundary cases and the 100-case smoke prefix. The
5,000-case validation is an explicit experiment command.

## Non-goals

- animal state or operations;
- random weed outcomes;
- movement, route choice or work-capacity estimation;
- portfolio choice, MILP or live-agent integration.

## Unresolved questions

None.
