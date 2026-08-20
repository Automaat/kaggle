"""Sync ladder episodes from Kaggle into `episodes/`, gzipped.

    uv run python replays_kaggle/fetch.py
    uv run python replays_kaggle/fetch.py --team 1234567
    uv run python replays_kaggle/fetch.py --limit 5

Idempotent: an episode already on disk costs one listing call and no download.
"""

import argparse
import gzip
import json
import pathlib
import shutil
import subprocess

HERE = pathlib.Path(__file__).resolve().parent
EPISODES = HERE / "episodes"
INDEX = HERE / "index.json"
COMPETITION = "kaggriculture"


def _cli(*arguments):
    """Kaggle prints a usage hint after the JSON, so keep only the JSON body."""
    out = subprocess.run(["kaggle", *arguments], capture_output=True, text=True, check=True).stdout
    start = out.find("[")
    end = out.rfind("]")
    if start < 0 or end < 0:
        return []
    return json.loads(out[start:end + 1])


def _submissions():
    return [row["ref"] for row in _cli("competitions", "submissions", "-c", COMPETITION, "--format", "json")]


def _team_submissions(team):
    return [row["ref"] for row in _cli("competitions", "team-submissions", str(team), "--format", "json")]


def _episodes(submission):
    return _cli("competitions", "episodes", str(submission), "--format", "json")


def _stored(episode_id):
    return EPISODES / f"episode-{episode_id}-replay.json.gz"


def _download(episode_id):
    subprocess.run(["kaggle", "competitions", "replay", str(episode_id), "-p", str(EPISODES), "-q"],
                   capture_output=True, text=True, check=True)
    raw = EPISODES / f"episode-{episode_id}-replay.json"
    with raw.open("rb") as source, gzip.open(_stored(episode_id), "wb") as target:
        shutil.copyfileobj(source, target)
    raw.unlink()
    return _stored(episode_id)


def _record(path):
    with gzip.open(path, "rt") as handle:
        replay = json.load(handle)
    info = replay.get("info", {})
    return {
        "episode": info.get("EpisodeId"),
        "teams": info.get("TeamNames"),
        "rewards": replay.get("rewards"),
        "seed": info.get("seed"),
        "steps": len(replay.get("steps", [])),
        "file": path.name,
    }


def _load_index():
    return json.loads(INDEX.read_text()) if INDEX.exists() else {}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--team", action="append", default=[], help="leaderboard team id, repeatable")
    parser.add_argument("--limit", type=int, default=0, help="stop after this many new downloads")
    args = parser.parse_args()

    EPISODES.mkdir(exist_ok=True)
    index = _load_index()
    submissions = _submissions() + [ref for team in args.team for ref in _team_submissions(team)]
    print(f"submissions: {submissions}")

    fetched = 0
    for submission in submissions:
        for episode in _episodes(submission):
            episode_id = episode["id"]
            if str(episode_id) in index and _stored(episode_id).exists():
                continue
            if args.limit and fetched >= args.limit:
                print(f"stopping at --limit {args.limit}")
                INDEX.write_text(json.dumps(index, indent=2, sort_keys=True))
                return
            path = _download(episode_id)
            record = _record(path)
            record["submission"] = submission
            record["type"] = episode.get("type")
            index[str(episode_id)] = record
            fetched += 1
            print(f"  {episode_id}  {record['teams']}  {record['rewards']}  seed={record['seed']}")

    INDEX.write_text(json.dumps(index, indent=2, sort_keys=True))
    print(f"\n{fetched} new, {len(index)} total in {INDEX.name}")


if __name__ == "__main__":
    main()
