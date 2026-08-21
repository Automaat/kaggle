# Routing policy training

This directory preserves the reinforcement-learning experiment used by the 1.13.0 routing policy.

## Files

- `selected.json` is the deployed checkpoint.
- `basic.json`, `zones.json`, and `memory.json` contain every training iteration and weight vector.
- `tools/train_routing.py` runs terminal-reward policy-gradient training.
- `main.py` contains dependency-free inference and the deployed weights.

The game agent does not import a machine-learning framework. Training uses the local Kaggriculture environment; inference is pure Python.

## Safety boundary

Rules still generate valid tasks, enforce carried-item requirements, reserve underfoot work, protect urgent work, and return units before the simulation closes. The policy can rank tasks only inside the best available safe class:

1. emergency work;
2. executable work under the unit;
3. nearby work;
4. distant work.

RL cannot move a lower class ahead of a higher class.

## Features

The nineteen feature positions are stable:

| Index | Value |
| ---: | :--- |
| 0 | negative task priority |
| 1 | negative raw Manhattan distance |
| 2 | negative zone-adjusted distance |
| 3 | negative distance to the nearest continuation task |
| 4 | number of continuation tasks within two tiles |
| 5 | target belongs to the unit's daily zone |
| 6 | target is the unit's previous target |
| 7-18 | one flag per task in `ROUTE_RL_TASKS` order |

The selected `zones` model masks feature 6. The `basic` model masks features 5 and 6. The `memory` model uses both.

## Reward and update

Each episode rewards the final money difference between the trained agent and its opponent. Every seed runs in both player seats. The trainer averages the two seats, standardizes the paired rewards inside the batch, clips the advantage to three standard deviations, and applies an Adam policy-gradient update.

Movement is not part of the reward. A previous heuristic reduced movement and lost money, so movement remains a diagnostic only.

## Reproduce the training

Create a frozen pre-RL opponent from the parent commit:

```bash
git show 4dfe1ae:kaggriculture/main.py > routing-baseline.py
```

Run the three independent models:

```bash
uv run python tools/train_routing.py --mode basic --opponent routing-baseline.py --output rl_models/basic.json --iterations 8 --batch 10 --seed-start 740000 --workers 8 --learning-rate 0.03 --temperature 0.2
uv run python tools/train_routing.py --mode zones --opponent routing-baseline.py --output rl_models/zones.json --iterations 8 --batch 10 --seed-start 740100 --workers 8 --learning-rate 0.03 --temperature 0.2
uv run python tools/train_routing.py --mode memory --opponent routing-baseline.py --output rl_models/memory.json --iterations 8 --batch 10 --seed-start 740200 --workers 8 --learning-rate 0.03 --temperature 0.2
```

The selected model is `zones`, iteration 4. Do not assume the last checkpoint is best.

## Data separation

| Purpose | Seeds |
| :--- | :--- |
| Basic training | 740000-740079 |
| Zone training | 740100-740179 |
| Memory training | 740200-740279 |
| Last-checkpoint screen | 740400-740439 |
| Checkpoint selection | 740500-740519 |
| Selected-checkpoint screen | 740600-740639 |
| Confirmation | 740700-740799 |
| Final untouched gate | 740900-741099 |
| Regression pool | 741100-741139 |

Future training must use new, non-overlapping seed ranges. Select checkpoints on a validation block, confirm once on untouched seeds, then run the full regression pool. Update `selected.json` and the default weights in `main.py` only after both gates pass.
