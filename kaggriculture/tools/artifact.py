import hashlib
import importlib.util
import json
import sys
import tarfile
import tempfile
import threading
from itertools import count
from pathlib import Path, PurePosixPath


MAX_ARCHIVE_BYTES = 2 * 1024 * 1024
MAX_MEMBERS = 100
_IMPORT_LOCK = threading.Lock()
_MODULE_COUNTER = count()


class LoadedAgent:
    def __init__(self, agent, module, package_modules, temporary_directory=None):
        self.agent = agent
        self.module = module
        self.package_modules = tuple(package_modules)
        self.temporary_directory = temporary_directory
        self.__name__ = getattr(agent, "__name__", "agent")

    def __call__(self, obs):
        return self.agent(obs)


def _safe_name(name):
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts


def _manifest_files(manifest):
    files = manifest.get("files")
    if manifest.get("schema") != 1 or not isinstance(files, list):
        raise ValueError("invalid manifest")
    indexed = {}
    for item in files:
        path = item.get("path")
        if not isinstance(path, str) or not _safe_name(path) or path in indexed:
            raise ValueError("invalid manifest path")
        if item.get("mode") != "0644":
            raise ValueError("invalid manifest mode")
        indexed[path] = item
    return indexed


def extract_archive(path, target):
    path = Path(path)
    target = Path(target).resolve()
    if path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("archive is too large")
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        if len(members) > MAX_MEMBERS:
            raise ValueError("archive has too many members")
        names = set()
        regular = {}
        total = 0
        for member in members:
            if not _safe_name(member.name) or member.name in names:
                raise ValueError("unsafe archive member")
            names.add(member.name)
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError("unsupported archive member")
            total += member.size
            if total > MAX_ARCHIVE_BYTES:
                raise ValueError("extracted data is too large")
            source = archive.extractfile(member)
            if source is None:
                raise ValueError("archive member is unreadable")
            regular[member.name] = (member, source.read())
    manifest_entry = regular.get("MANIFEST.json")
    if manifest_entry is None:
        raise ValueError("manifest is missing")
    manifest = json.loads(manifest_entry[1])
    expected = _manifest_files(manifest)
    actual = set(regular) - {"MANIFEST.json"}
    if actual != set(expected):
        raise ValueError("manifest file set differs")
    for name, item in expected.items():
        member, content = regular[name]
        if member.mode != 0o644 or member.size != item.get("size"):
            raise ValueError("manifest metadata differs")
        if hashlib.sha256(content).hexdigest() != item.get("sha256"):
            raise ValueError("manifest digest differs")
    target.mkdir(parents=True, exist_ok=True)
    for name, (_member, content) in regular.items():
        output = (target / name).resolve()
        if not output.is_relative_to(target):
            raise ValueError("archive path escapes target")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(content)
        output.chmod(0o644)
    return manifest


def _package_keys():
    return [name for name in sys.modules if name == "agent_2" or name.startswith("agent_2.")]


def _load_main(main_path, artifact_root, temporary_directory=None):
    main_path = Path(main_path).resolve()
    artifact_root = Path(artifact_root).resolve()
    token = hashlib.sha256(f"{main_path}:{next(_MODULE_COUNTER)}".encode()).hexdigest()[:16]
    module_name = f"_kaggriculture_agent_{token}"
    with _IMPORT_LOCK:
        saved_path = list(sys.path)
        saved_packages = {name: sys.modules[name] for name in _package_keys()}
        for name in saved_packages:
            sys.modules.pop(name, None)
        module = None
        loaded_packages = ()
        try:
            sys.path.insert(0, str(artifact_root))
            spec = importlib.util.spec_from_file_location(module_name, main_path)
            if spec is None or spec.loader is None:
                raise ImportError(str(main_path))
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            loaded_packages = tuple(sys.modules[name] for name in _package_keys())
            agent = module.agent
        finally:
            for name in _package_keys():
                sys.modules.pop(name, None)
            sys.modules.update(saved_packages)
            sys.modules.pop(module_name, None)
            sys.path[:] = saved_path
    return LoadedAgent(agent, module, loaded_packages, temporary_directory)


def load_artifact(path):
    path = Path(path).resolve()
    if path.is_dir():
        main_path = path / "main.py"
        if not main_path.is_file():
            raise FileNotFoundError(main_path)
        return _load_main(main_path, path)
    if path.name.endswith((".tar.gz", ".tgz")):
        temporary_directory = tempfile.TemporaryDirectory(prefix="kaggriculture-agent-")
        root = Path(temporary_directory.name)
        try:
            extract_archive(path, root)
            return _load_main(root / "main.py", root, temporary_directory)
        except BaseException:
            temporary_directory.cleanup()
            raise
    if path.suffix == ".py":
        return _load_main(path, path.parent)
    raise ValueError(f"unsupported agent artifact: {path}")
