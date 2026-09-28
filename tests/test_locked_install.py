"""Exercise composite installation from an unrelated consumer checkout, offline."""

import functools
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from threading import Thread
import zipfile

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("directory", [".", "capture-acceptance"])
@pytest.mark.parametrize("version", ["default", "v9.9.9", "invalid", "@main", "stale-lock"])
def test_installation_lock_and_explicit_override(tmp_path, directory, version):
    action_path = ROOT / directory
    action = yaml.safe_load((action_path / "action.yml").read_text())
    step = next(s for s in action["runs"]["steps"] if s.get("name") == "Install Maida")
    selected = action["inputs"]["maida-version"]["default"] if version in {"default", "stale-lock"} else version
    if version == "stale-lock":
        copied = tmp_path / "trusted action"
        (copied / "scripts").mkdir(parents=True)
        shutil.copy(ROOT / "scripts/install_maida.sh", copied / "scripts/install_maida.sh")
        shutil.copy(ROOT / "uv.lock", copied / "uv.lock")
        project = (ROOT / "pyproject.toml").read_text().replace(f"maida-ai=={selected[1:]}", "maida-ai==9.9.9")
        (copied / "pyproject.toml").write_text(project)
        action_path = copied / directory
        action_path.mkdir(exist_ok=True)
    bin_path = tmp_path / "bin"
    bin_path.mkdir()
    log = tmp_path / "arguments.json"
    executable = bin_path / "uv"
    executable.write_text(
        "#!/usr/bin/env python3\nimport json, os, subprocess, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "if args[0] == 'export':\n"
        "    raise SystemExit(subprocess.run([os.environ['REAL_UV'], *args]).returncode)\n"
        "manifest = Path(args[args.index('-r') + 1]) if '-r' in args else None\n"
        "Path(os.environ['ARGUMENT_LOG']).write_text(json.dumps({'args': args, 'manifest': str(manifest), 'requirements': manifest.read_text() if manifest else ''}))\n"
    )
    executable.chmod(0o755)
    temp_dir = tmp_path / "runner temp"
    temp_dir.mkdir()
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
        cwd=tmp_path, text=True, capture_output=True,
        env={**os.environ, "PATH": f"{bin_path}:{os.environ['PATH']}",
             "GITHUB_ACTION_PATH": str(action_path), "MAIDA_VERSION": selected,
             "ARGUMENT_LOG": str(log), "REAL_UV": shutil.which("uv"),
             "RUNNER_TEMP": str(temp_dir), "UV_OFFLINE": "1"},
    )
    if version in {"invalid", "stale-lock"} or (directory != "." and version == "@main"):
        assert result.returncode != 0
        assert not log.exists()
        assert not list(temp_dir.iterdir())
        return
    assert result.returncode == 0, result.stdout + result.stderr
    installed = json.loads(log.read_text())
    arguments = installed["args"]
    assert arguments[:2] == ["pip", "install"]
    assert "--python" in arguments
    if version == "default":
        assert "--require-hashes" in arguments
        assert "--only-binary=:all:" in arguments
        assert f"maida-ai=={selected[1:]}" in installed["requirements"]
        assert "--hash=sha256:" in installed["requirements"]
        assert "pytest==" not in installed["requirements"]
        assert not Path(installed["manifest"]).exists()
    else:
        requirement = "git+https://github.com/maida-ai/maida.git@main" if version == "@main" else f"maida-ai=={selected[1:]}"
        assert requirement in arguments
        assert "::warning::" in result.stdout
        assert "lock" in result.stdout.lower()
    assert not list(temp_dir.iterdir())


@pytest.mark.parametrize("group,engine", [("dev", False), ("e2e", True), ("maida", True)])
def test_locked_group_exports_are_isolated(group, engine, tmp_path):
    manifest = tmp_path / "requirements.txt"
    subprocess.run(
        ["uv", "export", "--project", str(ROOT), "--locked", "--offline",
         "--only-group", group, "--no-emit-project", "--output-file", str(manifest), "--quiet"],
        check=True,
    )
    text = manifest.read_text()
    assert ("maida-ai==" in text) is engine
    assert ("pytest==" in text) is (group != "maida")
    assert "--hash=sha256:" in text


@pytest.mark.parametrize("corrupt", [False, True])
def test_default_install_uses_selected_interpreter_without_test_dependencies(tmp_path, corrupt):
    # A tiny local registry exercises real hash-checked installation without external services.
    selected = yaml.safe_load((ROOT / "action.yml").read_text())["inputs"]["maida-version"]["default"]
    version = selected[1:]
    index = tmp_path / "index"
    package_index = index / "simple/maida-ai"
    package_index.mkdir(parents=True)
    wheel = index / f"maida_ai-{version}-py3-none-any.whl"
    info = f"maida_ai-{version}.dist-info"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("maida/__init__.py", "# Installation fixture\n")
        archive.writestr(f"{info}/METADATA", f"Metadata-Version: 2.3\nName: maida-ai\nVersion: {version}\n")
        archive.writestr(f"{info}/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr(f"{info}/RECORD", "")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    (package_index / "index.html").write_text(
        f'<a href="../../{wheel.name}#sha256={digest}">{wheel.name}</a>'
    )

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(index)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        project = tmp_path / "trusted action"
        project.mkdir()
        (project / "pyproject.toml").write_text(
            '[project]\nname = "fixture"\nversion = "0"\nrequires-python = ">=3.10"\n'
            '[tool.uv]\npackage = false\n'
            f'[dependency-groups]\nmaida = ["maida-ai=={version}"]\n'
        )
        env = {**os.environ, "UV_INDEX_URL": f"http://127.0.0.1:{server.server_port}/simple",
               "UV_CACHE_DIR": str(tmp_path / "cache"), "UV_OFFLINE": "0"}
        subprocess.run(["uv", "lock", "--project", str(project), "--python", sys.executable], env=env, check=True)
        if corrupt:
            with zipfile.ZipFile(wheel) as archive:
                members = {name: archive.read(name) for name in archive.namelist()}
            members["maida/__init__.py"] = b"# Modified after locking\n"
            with zipfile.ZipFile(wheel, "w") as archive:
                for name, contents in members.items():
                    archive.writestr(name, contents)
        environment = tmp_path / "runtime"
        subprocess.run(["uv", "venv", "--python", sys.executable, str(environment)], check=True)
        result = subprocess.run(
            ["bash", str(ROOT / "scripts/install_maida.sh")], cwd=tmp_path,
            env={**env, "PATH": f"{environment / 'bin'}:{os.environ['PATH']}",
                 "MAIDA_ACTION_ROOT": str(project), "MAIDA_VERSION": selected,
                 "UV_CACHE_DIR": str(tmp_path / "install-cache")},
            capture_output=True, text=True,
        )
        if corrupt:
            assert result.returncode != 0
            assert "hash" in result.stderr.lower()
            return
        assert result.returncode == 0, result.stdout + result.stderr
        result = subprocess.run(
            [str(environment / "bin/python"), "-c",
             "import importlib.metadata, importlib.util; "
             f"assert importlib.metadata.version('maida-ai') == {version!r}; "
             "assert importlib.util.find_spec('pytest') is None"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
