# Pulling replays from Kaggle

Everything here is the stock Kaggle CLI, already installed as a dependency. No scraping, no notebook. The plan for what to build on top is in [PLAN.md](PLAN.md).

Authentication is the usual `~/.kaggle/kaggle.json`. If a command prints a 401, that file is missing or stale.

## 1. Find your submissions

```bash
uv run kaggle competitions submissions -c kaggriculture --format json
```

Returns one entry per submission. `ref` is the submission id — `55630506` is the 0.22.0 submission.

## 2. List that submission's episodes

```bash
uv run kaggle competitions episodes 55630506 --format json
```

One entry per ladder game: `id`, `createTime`, `endTime`, `state`, `type`. `EPISODE_TYPE_VALIDATION` is the one Kaggle runs to accept the submission; `EPISODE_TYPE_PUBLIC` are the real ladder games.

## 3. Download one replay

```bash
uv run kaggle competitions replay 94619184 -p replays_kaggle/episodes
```

Writes `episode-<id>-replay.json`. **About 22 MB each**, so keep them out of git — `replays_kaggle/episodes/` is already ignored.

## 4. Read the replay

```python
import json

replay = json.load(open("replays_kaggle/episodes/episode-94619184-replay.json"))

replay["info"]["TeamNames"]   # ['Marcin Skalski', 'Howey do it']
replay["rewards"]             # [47160.0, 49912.0] — final money, our seat first
replay["info"]["seed"]        # 1047500473
len(replay["steps"])          # 720, one per turn

step = replay["steps"][0][0]  # [turn][seat]
step["observation"]           # day, hour, farms, market, town, player, private
step["action"]                # what that seat did on that turn
```

Both seats' observations and actions are in the file, so both farms, the market and the town are readable turn by turn.

## 5. Re-run a ladder game locally

`info.seed` is the same seed `tools/runner.py` takes, so the map from any ladder game can be replayed against a candidate:

```bash
uv run python tools/play.py main.py champion 1047500473
```

## 6. Our own logs from a real match

```bash
uv run kaggle competitions logs 94619184 0
```

Agent index `0` is the first seat. This is the only view we get of what our agent printed while playing a stranger.

## 7. Opponents' replays

```bash
uv run kaggle competitions leaderboard kaggriculture --show
uv run kaggle competitions team-submissions <team_id> --format json
```

The team id leads to that team's active submissions, and each submission id goes back into step 2.
