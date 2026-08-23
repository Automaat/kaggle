import argparse
import gzip
import hashlib
import importlib.metadata
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

from kaggle_environments import make


ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "agents_1.0.x/v1_14_0_central_herd.py"
BASELINE_COMMIT = "b74a3ea6254c049199c588abb2c6b9e0b4a6e321"


def _sha256(content):
    return hashlib.sha256(content).hexdigest()


def _source_commit():
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def _configuration():
    environment = make("kaggriculture", configuration={"episodeSteps": 720})
    return json.loads(json.dumps(environment.configuration))


def _payload(source):
    source = Path(source)
    paths = [source / "main.py", *sorted((source / "agent_2").glob("*.py"))]
    files = {}
    for path in paths:
        if not path.is_file() or path.is_symlink():
            raise ValueError(path)
        relative = path.relative_to(source).as_posix()
        files[relative] = path.read_bytes()
    files[f"frozen/{BASELINE.name}"] = BASELINE.read_bytes()
    return files


def _manifest(files, source_commit, stage="37.0", candidate="2.0.0-shell"):
    return {
        "schema": 1,
        "stage": stage,
        "candidate": candidate,
        "source_commit": source_commit,
        "baseline_commit": BASELINE_COMMIT,
        "baseline_sha256": _sha256(files[f"frozen/{BASELINE.name}"]),
        "python_version": ".".join(str(value) for value in sys.version_info[:3]),
        "kaggle_environments_version": importlib.metadata.version("kaggle-environments"),
        "configuration": _configuration(),
        "replay_reference": {"episode": 96047508, "townCenterSellInterval": 24},
        "files": [
            {"path": path, "size": len(content), "mode": "0644", "sha256": _sha256(content)}
            for path, content in sorted(files.items())
        ],
    }


def _tar_info(path, content):
    info = tarfile.TarInfo(path)
    info.size = len(content)
    info.mode = 0o644
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    return info


def build_archive(
    source, output, source_commit=None, stage="37.0", candidate="2.0.0-shell"
):
    files = _payload(source)
    manifest = _manifest(files, source_commit or _source_commit(), stage, candidate)
    files["MANIFEST.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for path, content in sorted(files.items()):
            archive.addfile(_tar_info(path, content), io.BytesIO(content))
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as raw:
        with gzip.GzipFile(
            filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0,
        ) as compressed:
            compressed.write(tar_buffer.getvalue())
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("output")
    parser.add_argument("--stage", default="37.0")
    parser.add_argument("--candidate", default="2.0.0-shell")
    args = parser.parse_args()
    manifest = build_archive(
        args.source, args.output, stage=args.stage, candidate=args.candidate,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
