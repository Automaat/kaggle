import gzip
import io
import json
import pathlib
import sys
import tarfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))

from artifact import extract_archive


def _archive(path, members):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for info, content in members:
            archive.addfile(info, io.BytesIO(content) if content is not None else None)
    with gzip.GzipFile(filename="", mode="wb", fileobj=path.open("wb"), mtime=0) as output:
        output.write(buffer.getvalue())


def _file(name, content=b"x"):
    info = tarfile.TarInfo(name)
    info.size = len(content)
    info.mode = 0o644
    return info, content


@pytest.mark.parametrize("name", ["../main.py", "/main.py"])
def test_extraction_rejects_escaping_paths(tmp_path, name):
    archive = tmp_path / "unsafe.tar.gz"
    _archive(archive, [_file(name)])
    with pytest.raises(ValueError):
        extract_archive(archive, tmp_path / "output")


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE])
def test_extraction_rejects_links_and_devices(tmp_path, kind):
    archive = tmp_path / "link.tar.gz"
    info = tarfile.TarInfo("main.py")
    info.type = kind
    info.linkname = "target"
    _archive(archive, [(info, None)])
    with pytest.raises(ValueError):
        extract_archive(archive, tmp_path / "output")


def test_extraction_rejects_duplicate_members(tmp_path):
    archive = tmp_path / "duplicate.tar.gz"
    _archive(archive, [_file("main.py"), _file("main.py")])
    with pytest.raises(ValueError):
        extract_archive(archive, tmp_path / "output")


def test_extraction_requires_manifest(tmp_path):
    archive = tmp_path / "missing.tar.gz"
    _archive(archive, [_file("main.py")])
    with pytest.raises(ValueError):
        extract_archive(archive, tmp_path / "output")


def test_extraction_rejects_manifest_digest_mismatch(tmp_path):
    archive = tmp_path / "digest.tar.gz"
    manifest = {
        "schema": 1,
        "files": [{"path": "main.py", "size": 1, "mode": "0644", "sha256": "0" * 64}],
    }
    _archive(archive, [
        _file("MANIFEST.json", json.dumps(manifest).encode()),
        _file("main.py"),
    ])
    with pytest.raises(ValueError):
        extract_archive(archive, tmp_path / "output")


def test_extraction_rejects_too_many_members(tmp_path):
    archive = tmp_path / "many.tar.gz"
    _archive(archive, [_file(f"file-{index}", b"") for index in range(101)])
    with pytest.raises(ValueError):
        extract_archive(archive, tmp_path / "output")


def test_extraction_rejects_excessive_data(tmp_path):
    archive = tmp_path / "large.tar.gz"
    _archive(archive, [_file("main.py", b"x" * (2 * 1024 * 1024 + 1))])
    with pytest.raises(ValueError):
        extract_archive(archive, tmp_path / "output")
