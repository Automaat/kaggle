# Agent 2.0 A1b inventory-ledger plan

Status: execution plan for A1b only.

## Objective

Extend the accepted A1a market ledger with exact shed and unit inventory
transfers and phase availability. Prove that a market purchase or hire cannot
serve a unit action from the same turn. Keep crop and animal biology outside
this stage.

Source predecessor: `5ffb2e4`. A score comparator is not applicable because
A1b cannot change a live-agent action.

## Files

The implementation commit changes only:

1. `tools/economics/inventory_ledger.py`;
2. `tools/economics/validate_inventory_ledger.py`;
3. `tests/test_economic_inventory_ledger.py`.

The result commit adds one JSON file and updates `EXPERIMENTS.md`.

## Model boundary

`inventory_ledger.py` imports only the standard library and the accepted
`market_ledger.py`. It does not import Kaggle, 1.14.0 or Agent 2.0.

It exports frozen:

- `UnitInventory`, with ordered unique `(item, positive_count)` entries;
- `InventoryState`, with one A1a `MarketState` and the ordered inventories of
  farmer then hands for both players;
- `InventoryEvent`;
- `InventoryTransition`, with `after_units`, accepted A1a `market_transition`
  and `after_town`;
- `InventoryDayEnd`, with state and discarded overflow by player and item.

`InventoryState` requires exactly `hands + 1` unit inventories per player. Shed
uses the A1a fixed 12-item vector. Unit entry order is semantic because `DROP`
and end-of-day overflow process Python dictionary insertion order.

`UnitInventory` accepts only the 12 `SHED_ITEMS`. Day-end discarded totals are
a fixed 12-item vector per player. Detailed loss events retain player, unit and
item insertion order.

It also exports fixed-vector `add`, `take`, `transfer` and `remaining_capacity`
helpers. The executable model and later MILP fixtures must use the same helpers.

## Unit inventory phase

`apply_unit_transfer(...)` handles one action and returns handled status, updated
shed and unit inventory, plus one `InventoryEvent`. A1c and A1d must call it
inside their shared per-unit dispatcher. They must not run a second full pass
over unit actions.

`apply_inventory_phase(
state, unit_actions, shed_adjacency, market_queues, trace=False
)` performs:

1. farmer then existing hands for player 0;
2. farmer then existing hands for player 1;
3. freeze `after_units`;
4. call accepted A1a `apply_market_phase`;
5. append one empty unit inventory for each accepted `HIRE`;
6. freeze `after_town` without advancing the source step.

`unit_actions` is an exact two-player tuple. Each player entry contains one raw
farmer action and raw hands actions. Hands actions follow simulator behavior: a
non-list becomes empty, missing entries are no-ops and excess entries are
ignored. `shed_adjacency` is an exact two-player tuple with exactly
`hands + 1` booleans per player. The future board model computes each fact from
the position immediately before that unit action. A1b exactness is conditional
on those spatial facts. All validation uses `boardSize=10` and derives the fact
from the injected position before comparing it with the input.

Only these independently testable unit operations are handled by A1b:

- `PICKUP item [quantity]` at the shed: remove up to available shed quantity
  and append or increase the ordered unit entry;
- `DROP` at the shed: visit every unit entry in insertion order, deposit up to
  capacity, discard all overflow and clear the unit inventory;
- `PLACE item [quantity]` at the shed: deposit only the accepted amount and
  retain overflow in the unit inventory;
- `PASS`: handled with no inventory effect.

Every other operation returns not-handled. A1c or A1d owns its effects.
Matching animal placement has priority in A1d; only a declined animal placement
can reach A1b shed `PLACE`.

The raw parser matches simulator list checks and `int()` conversion. Exceptions
not caught by the simulator remain exception-compatible, including `ValueError`
for invalid text quantities. Commands are not mutated. Unknown or unhashable
items follow the same exception or no-op path as the simulator.

`InventoryEvent` contains player, unit index, operation, item, requested,
accepted and discarded quantity, source, destination and failure reason.

Phase separation is the purchase-delay contract. `after_units` cannot contain
current-turn market purchases or hires. `after_town` can. The accepted HIRE
count comes from the A1a hand-count difference, never trace. With `trace=False`,
the result and appended inventories are unchanged. Availability in a real next
observation remains conditional on the later phases. In particular, a hand
hired on source step 23 is removed by end-of-day before the next observation.

## End-of-day inventory phase

`apply_inventory_day_end(state, trace=False)` processes player order, then
farmer-to-hands order, then unit-entry insertion order. It deposits up to shed
capacity, discards overflow, clears every unit inventory, keeps only one empty
farmer inventory, clears hands and resets `hires_today`. It does not model crop,
animal, weed, spawn or shop effects and does not advance the source step.

The caller composes it only when `(source_step + 1) % 24 == 0`. Source step 23
applies it. Source step 718 does not. Step 719 has no processed agent action or
automatic final dump.

This function is composed after the future A1c and A1d refresh phases.

## Differential validator

`validate_inventory_ledger.py` creates invariant-valid two-player simulator
state. It reuses the A1a injector base, preserves unit dictionary insertion
order and verifies a fresh reset and injected snapshot before every case. It
uses fixed positions `(4,4)` for shed-adjacent commands and `(0,0)` otherwise.
All tiles are empty because animal placement belongs to A1d.

For each case it invokes, in order:

1. simulator `_apply_unit_action()` for existing units;
2. take a deep ordered snapshot after units;
3. simulator `_process_market()`;
4. simulator `_town_consume()`;
5. simulator `_end_of_day()` for day-end cases.

It compares A1a fields plus every ordered unit inventory after the unit phase,
after town and after day end. The day-end fixture uses empty boards,
`weedSpawnChance=0` and a controlled shop unlock interval so unrelated state
cannot change the projection. It compares dictionary insertion order, not only
item totals.

Expected simulator and model exceptions must have the same type and phase.
Matched expected exceptions are counted separately. Unexpected exceptions are
failures. A1a regression invariants require the market input to equal
`after_units.market`, the after-town market to equal direct A1a output and
`trace=False` to produce the same state. Item-balance invariants cover units,
shed, market, town consumption and day-end loss.

Use registered RNG seed `3,960,000`. Boundary cases cover:

- all 12 shed items, both players and every existing unit position;
- adjacent and non-adjacent transfer attempts;
- missing, zero, negative, boolean, float, text and excess quantities;
- empty, partial, exact and over-requested pickup;
- empty shed, capacity minus one, capacity and synthetic over-capacity shed;
- multi-item insertion order and overflow difference between `DROP` and
  `PLACE`;
- delete-last-entry then add-again insertion order;
- two units competing for the same shed stock or final capacity;
- current-turn `BUY_PRODUCT`, `BUY_ANIMAL`, `BUY_SEED` and `HIRE` latency;
- `DROP` then same-turn `SELL`, and `PICKUP` then same-turn `SELL`;
- `DROP` filling the shed before `BUY_PRODUCT` or `BUY_ANIMAL`;
- purchase then pickup on the next invocation;
- day-end farmer and hand order, item order and overflow discard;
- source-step-23 purchase or hire followed by full end-of-day reset;
- missing and excess hand actions, unknown items and unhashable items;
- one market phase after transfer on source steps `0`, `23`, `24` and `718`.

Run exactly 5,000 deterministic stratified invariant-valid cases with IDs
`R00000..R04999`. Generator schema `inventory-v1` cycles operation, player,
farmer or hand seat, item, adjacency, capacity state and source step. Source
steps cycle through `0`, `23`, `24` and `718`. Every 100-case prefix contains
all operations, both players, adjacent and non-adjacent cases, both capacity
edges and all four source-step phases. Registered RNG seed is used only for
values inside each fixed stratum. Canonical typed input JSON includes schema,
seed and all generated cases and produces `input_sha256`.

The result JSON contains boundary and random counts, compared fields, matched
expected exceptions, first mismatch with trace, unexpected failures, elapsed
time, environment version, generator schema, input hash and source hashes. A
mismatch or unexpected failure exits nonzero.

## Gates

- boundary and 100-case smoke validation pass before the full run and stop on
  the first error;
- all tests pass;
- Ruff passes for changed files;
- zero field mismatches and unexpected failures for every boundary and 5,000
  registered random cases;
- two 100-case smoke runs match after elapsed time is removed;
- full validation, including environment construction, finishes within 120
  seconds;
- model source has no simulator, Agent 2.0 or 1.14.0 import;
- adversarial review has no surviving issue;
- signed conventional implementation commit;
- separate signed result commit.

The normal test suite runs boundary cases and the 100-case smoke prefix. The
5,000-case validation is an explicit experiment command, not part of normal
tests.

Simulator version 1.32.7 is the executable source of truth. `RULES.md` says
shed `PLACE` loses overflow, but the simulator retains it. A1b matches the
simulator and records this discrepancy without changing the rules document.

## Non-goals

- movement and route choice;
- plant creation, watering, harvest, fertilization or decay;
- animal feed, care, production, fertilizer or escape;
- full end-of-day state and step advancement;
- portfolio choice, MILP or live agent integration.

## Unresolved questions

None.
