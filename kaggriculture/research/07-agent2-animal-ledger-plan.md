# Agent 2.0 A1d animal-ledger plan

Status: execution plan for A1d only.

## Objective

Extend accepted A1c with exact movement, structures, animals, deterministic
weeds, hiring positions and complete end-of-day transitions. Validate the full
shared dispatcher against simulator 1.32.7 before A2 uses animal production,
feed demand or work capacity in the MILP.

Source predecessor: `ba369b6`. A score comparator is not applicable because
A1d cannot change a live-agent action.

## Files

The implementation commit changes only:

1. `tools/economics/animal_ledger.py`;
2. `tools/economics/validate_animal_ledger.py`;
3. `tests/test_economic_animal_ledger.py`.

The result commit adds
`research/round39_6_animal_ledger_validation.json` and updates
`EXPERIMENTS.md`.

## State and composition

`animal_ledger.py` imports only the standard library and accepted A1a-A1c
modules. It does not import Kaggle, Agent 2.0, 1.14.0 or a solver.

It exports frozen:

- `AnimalSpec` for the three simulator animal tables;
- `AnimalTile` for placed animal state;
- `AnimalConfig` for the episode seed, weed rate and shop unlock interval;
- `AnimalState` containing accepted `CropState`, exact unit positions and two
  animal overlays;
- `AnimalEvent` and `AnimalTransition` for phase results;
- `apply_animal_player()`, `apply_animal_phase()` and
  `advance_animal_state()`.

Each 100-cell overlay contains `None`, `"COOP"`, `"PASTURE"` or an
`AnimalTile`. The accepted crop board stores the matching delegated marker:
`"STRUCTURE"` or `"ANIMAL"`. Construction rejects an overlay that disagrees
with its crop board. This keeps A1c unchanged and prevents an animal cell from
also holding a crop.

Positions contain the farmer followed by every current hand. Their lengths
must equal accepted ordered unit inventories and `PlayerAccount.hands + 1`.
Positions are part of the model state. They are no longer supplied as an
external fact.

## Shared unit dispatcher

`apply_animal_player()` matches `_apply_unit_action()` in this order:

1. compute atomic `PLANT` blocking from all raw farmer and hand requests;
2. execute the farmer and each listed existing hand in order;
3. apply cardinal movement and stop that unit action;
4. apply matching animal `PLACE` before shed placement, including the consumed
   no-op when the matching structure exists but the unit lacks the animal;
5. call accepted A1b shed transfer using the current position;
6. call accepted A1c crop operation;
7. apply animal harvest, structure dig, build, feed, fertilizer collection and
   care;
8. preserve the exact partial state on the original exception type.

Movement onto locked cells is legal. Movement outside the board is a no-op.
Units can share a cell. Missing hand actions are no-ops. Excess hand actions
participate in atomic planting but do not execute. Unknown operations are
no-ops. Raw parser exceptions match the simulator.

Animal placement requires a matching empty structure. It consumes one carried
animal and replaces the structure with a new animal at the current day. A
matching placement request never falls through to shed placement. A
nonmatching placement can use accepted A1b shed placement when the unit stands
at a shed-access cell.

`DIG` removes an empty structure but cannot remove a placed animal. Animal
`HARVEST` moves all held product into the acting unit. `FEED` consumes one
carried wheat once per day. `COLLECT_FERTILIZER` moves one available unit into
the acting unit. `CARE` is accepted once per day.

## Market, hire and land phase

After both players act, `apply_animal_phase()` calls accepted A1a market and
town logic. It applies A1c land unlocks and accepted hire inventories. Each
accepted hire receives the exact spawn position selected from the four shed
access cells by current occupancy and NW, NE, SW, SE order. Multiple hires in
one market phase observe earlier accepted hires.

Animal purchases remain exact A1a shed mutations. They cannot be placed until
the next observation. Land purchases cannot serve a current unit action.

## Decay and complete end of day

The phase applies A1c plant decay after town demand. At day-end it executes:

1. accepted A1c crop refresh;
2. animal refresh in row-major player order;
3. deterministic weed spawning in player and row-major order;
4. accepted A1b inventory drop and overflow discard;
5. farmer reset, hand removal and hire reset;
6. deterministic town shop unlock.

Animal refresh uses `next_day = current_day + 1`. Two unfed days remove the
animal and preserve its structure. Production follows exact first-day and
interval tables. It is capped by `max_held`. A pending care bonus is consumed
only on a fed production day. Care from the current fed day becomes the next
pending bonus. Every surviving animal makes one fertilizer available, then
clears daily feed and care flags.

Weed and shop randomness uses
`random.Random((episode_seed * 1_000_003) ^ current_day)`. A random weed draw
occurs only for an empty cell. Both player boards consume draws before an
optional shop choice from sorted shop names. `AnimalConfig` validates a finite
weed probability in `0..1` and a positive shop interval.

`advance_animal_state()` increments only the accepted source step after a phase.
It rejects advancement after source step 718. The phase result keeps the source
step at the processed action, matching A1a-A1c phase boundaries.

## Differential validator

`validate_animal_ledger.py` injects the complete model state, actions,
configuration and episode seed into a fresh simulator. It projects every
market, inventory, crop, animal and position field back into `AnimalState`.

For each case it compares:

1. each player's partial unit state and exception type;
2. complete `after_units`;
3. accepted market and town state;
4. hired inventories, exact spawn positions and land unlocks;
5. plant decay;
6. crop refresh on day-end turns;
7. animal refresh;
8. weed positions;
9. inventory overflow, unit and position reset;
10. shop unlock and complete end-of-day state.

The validator also checks direct A1a-A1c composition, animal-product balance,
wheat consumption, fertilizer collection, animal escape and position
invariants.

Boundary cases cover every animal at ages around first production and two
intervals; zero, cap and near-cap held yield; fed, unfed, cared and pending
bonus combinations; feed before care and care before feed; harvest before and
after production; repeated feed, care, harvest and fertilizer collection;
matching and nonmatching placement; missing carried animals; structure build
and dig; animal dig; all movements at board edges and onto locked cells; same
cell unit ordering; malformed actions and partial exceptions; accepted and
rejected hires; all four spawn cells and occupancy ties; animal purchase delay;
land purchase delay; shed overflow; source steps 0, 23, 24, 717 and 718; two
unfed-day escape; day-29 production; weed rates 0 and 1; random partial-empty
boards; duplicate shop unlocks and the eight-shop cap.

Run exactly 5,000 deterministic stratified cases with IDs
`R00000..R04999`, generator schema `animal-v1` and RNG seed `3,980,000`.
Strata cover animal, operation, player, farmer or hand, tile state, feed state,
care state, held-yield state, fertilizer state, source-step phase, weed regime,
shop-unlock boundary and land count. A coverage manifest records every declared
dimension. The canonical typed input produces `input_sha256`.

## Gates

- reviewed plan commit before implementation;
- boundary and 100-case smoke pass before the full run;
- all tests pass;
- Ruff passes for changed files;
- zero field mismatches and unexpected failures across boundary and 5,000
  stratified cases;
- two 100-case smoke runs match after elapsed time is removed;
- full validation finishes within 120 seconds;
- model imports no simulator, Agent 2.0, 1.14.0 or solver;
- adversarial review has no surviving issue;
- signed conventional implementation commit;
- separate signed result commit.

## Non-goals

- economic portfolio choice or MILP;
- route optimization or calibrated future work capacity;
- live-agent integration;
- Kaggle runtime optimization.

## Unresolved questions

None.
