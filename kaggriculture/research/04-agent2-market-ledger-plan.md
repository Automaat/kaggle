# Agent 2.0 A1a market-ledger plan

Status: execution plan for A1a only.

## Objective

Build an independent, deterministic model of cash, market orders, town demand
and price changes. Validate it against `kaggle-environments==1.32.7`. Do not
change live agent actions and do not add SciPy.

Source predecessor: `b2b8d5c`. A score comparator is not applicable because
A1a cannot change a live-agent action.

## Files

The implementation commit changes only:

1. `tools/economics/market_ledger.py`;
2. `tools/economics/validate_market_ledger.py`;
3. `tests/test_economic_market_ledger.py`.

The result commit adds one JSON file and updates `EXPERIMENTS.md`.

## Independent model

`market_ledger.py` uses only the Python standard library. It does not import
the simulator, 1.14.0 or Agent 2.0. Verified game constants can be copied into
immutable tables and pinned by tests.

The module exports these frozen dataclasses:

- `MarketState`;
- `PlayerAccount`;
- `MarketConfig`;
- `MarketTransition`;
- `OrderEvent`;
- `TownEvent`.

It also exports pure `resolve_market_params`, `market_price`, `sell_quote`,
`buy_quote` and `apply_market_phase` functions. A later MILP builds marginal
cost and revenue tables from the quote functions. It does not depend on raw
queue parsing or trace output.

Immutable input state contains:

- absolute source `step`;
- market inventory for all nine products;
- two player accounts with money, shed counts, seed counts, hires today,
  total unlocked-quadrant count `1..4` and hand count;
- unlocked shop instances, including duplicates;
- resolved market parameters and configuration values for order limit, shed
  capacity, hire multiplier and both town intervals.

All resource vectors are tuples in fixed exported order. Market inventory has
the nine products. Shed has the nine products followed by the three animals.
Seeds have the five crops. Mapping constructors reject missing keys, extra keys,
non-exact integers and negative private counts. Market inventory can be
negative. Synthetic over-capacity shed states are allowed and tagged by the
validator.

`apply_market_phase(state, queues, trace=False) -> MarketTransition` preserves
the input `source_step`. Its `after_town` field is the state immediately after
town demand and before plant decay or end-of-day. It does not claim a complete
game step. Purchased goods and structural counters are present in this phase
state. A1b later applies the remaining phases and advances the step.

The public function accepts the two raw market queues. It preserves simulator
parsing behavior for list type, malformed orders, `int()` quantity conversion,
positive quantities and queue truncation.

State construction raises `TypeError` for wrong exact scalar or container types
and `ValueError` for invalid keys, lengths, ranges or private counts. Because
the state is immutable, a failed transition has no partial result. Raw-order
exceptions not caught by the simulator, including `OverflowError` from
`int(infinity)`, remain exception-compatible.

## Transition

For each order index:

1. snapshot both cash balances and market inventory;
2. process `HIRE` and `BUY_LAND` once in player order;
3. quote one remaining unit for both players from the same pre-commit market;
4. commit player 0 and player 1 using those frozen quotes;
5. stop only the order that fails;
6. continue until both orders end;
7. refresh all prices.

Exact commit rules include:

- sell only from shed;
- pre-sell price;
- no market-inventory increase for a sale at `$1`;
- `BUY_PRODUCT` only for wheat and fertilizer;
- post-buy price;
- shed capacity for products and animals but not seeds;
- fixed seed and animal costs;
- insufficient cash aborts the rest of that order;
- Fibonacci daily hire cost;
- land prices `$1,000`, `$2,000`, `$4,000` and no fourth purchase.

After all order indexes, town demand uses the source absolute step. Each shop
instance consumes independently. Single-product shops consume two units. The
town centre consumes every product except fertilizer. Refresh all prices and
return the pre-decay phase state.

## Trace contract

With `trace=True`, the result stores immutable traces with:

- order index;
- cash and full market inventory before the index;
- a separate atomic event for `HIRE` or `BUY_LAND`, with acceptance, inferred
  failure reason and account change;
- each unit number;
- each player's operation, item, quoted price, item inventory before quote,
  accepted flag and failure reason;
- cash and inventory after the index;
- a town event for every attempted consumption, including shop instance,
  product, quantity and market change.

With `trace=False`, event tuples are empty. On mismatch the validator reruns only
that fixture with trace enabled. Trace cannot affect the phase result.

## Differential validator

`validate_market_ledger.py` creates one real two-player Kaggriculture
environment with `boardSize=10`, injects matched market, private, farm, shop and
step state, then invokes the simulator `_process_market()` and
`_town_consume()` reference phases directly. It does not invoke decay,
end-of-day or the framework step rewrite. Before each phase call it verifies
that the injected projection equals the model input.

The scaffold has valid 10-by-10 tiles, farmer positions, hand positions and one
private inventory per unit. The validator compares exactly:

- source step;
- all market inventory and derived prices;
- both money balances;
- both complete shed and seed vectors;
- `hires_today`, hand count and unlocked-land count.

It does not compare hand positions, tile layout, unit inventories or other
A1b state.

Use registered RNG seed `3,950,000`. Run:

- exhaustive one-order cases for every operation, item, quantity `0..4`, both
  seats and boundary cash;
- same-item two-player buys and sells;
- different-item lockstep orders;
- order failure followed by the other player's continuation;
- ten- and eleven-order queues;
- legal shed capacity `1` and `100`, with occupancy at capacity minus one,
  capacity and capacity plus one;
- all hire and land cost boundaries;
- market inventory around `I0`, `I0 +/- T`, the `$1` floor and negative stock;
- every price shape and sparse `marketParams` overrides;
- source steps `0`, `23`, `24`, `47`, `48`, `717` and `718` plus custom town
  intervals;
- duplicate shops and every simulator shop type, including wheat demand at an
  ice cream shop;
- parser cases for outer and inner tuples, empty and extra fields, missing
  values, `bool`, float truncation, numeric text, invalid operation and item;
- the `99,999`-iteration safety boundary and the next quantity;
- simultaneous buy and sell of the same product;
- exactly 10,000 deterministic randomized reachable projections.

Invalid inputs that make simulator 1.32.7 raise, such as an unhashable item or
infinite quantity, are separate exception-parity tests. They are excluded from
the zero-failure random domain.

Deterministic boundary IDs start with `B`. Random IDs are
`R00000..R09999`; the smoke suite uses the fixed prefix `R00000..R00099`.
Canonical sorted JSON of all generated inputs produces `input_sha256`. The
validator writes fixture counts by reachable and synthetic layer, the exact
compared-field list, first mismatch with complete input and trace, elapsed time,
environment version, input hash and hashes of model and simulator source. A
mismatch exits nonzero.

## Unit tests

- price function matches the simulator over registered boundary inventories;
- sparse parameter resolution matches the simulator;
- buy then sell against unchanged inventory nets zero;
- two players using the same item and operation receive the same pre-commit
  quote; simultaneous buy and sell use their different quote rules;
- later units receive changed quotes;
- floor sales pay but do not add inventory;
- failed units abort only their order;
- malformed and excess orders match simulator behavior;
- shed, cash, hire and land boundaries match;
- town timing and duplicate demand match;
- `linear`, `sq`, `sqrt`, `log`, `log10`, `hinge`, unknown-shape fallback and
  sparse `I0` and `T` overrides match;
- inputs and traces are immutable;
- validator smoke test compares exactly the 100-case registered prefix.

## Gates

- full tests and Ruff pass;
- zero field mismatches in all registered fixtures and 10,000 randomized
  transitions;
- zero simulator failures;
- model source has no simulator, Agent 2.0 or 1.14.0 imports;
- deterministic JSON from the same seed except elapsed time;
- two smoke runs have identical canonical JSON after removing elapsed time;
- smoke validation finishes within 10 seconds and the full run within 120
  seconds on the registered local environment;
- signed conventional implementation commit;
- separate signed result commit.

## Non-goals

- unit actions, crops, animals outside market purchase, decay and end-of-day;
- route or work-capacity estimates;
- portfolio choice or MILP;
- live agent integration;
- Kaggle runtime optimization.

## Unresolved questions

None.
