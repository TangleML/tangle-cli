from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[1]


def _build_wheel(tmp_path: Path, *args: str) -> Path:
    out_dir = tmp_path / "dist"
    command = ["uv", "build", "--wheel", "--out-dir", str(out_dir), *args]
    subprocess.run(command, cwd=_REPO_ROOT, check=True, text=True, capture_output=True)
    wheels = sorted(out_dir.glob("*.whl"))
    assert wheels, f"no wheel built by {' '.join(command)}"
    return wheels[-1]


def _write_import_stubs(path: Path) -> None:
    path.mkdir()
    (path / "httpx.py").write_text("", encoding="utf-8")
    (path / "platformdirs.py").write_text("", encoding="utf-8")
    (path / "cyclopts.py").write_text(
        "class App:\n"
        "    def __init__(self, *args, **kwargs): pass\n"
        "    def command(self, obj=None, **kwargs):\n"
        "        if obj is not None:\n"
        "            return obj\n"
        "        def decorator(fn): return fn\n"
        "        return decorator\n"
        "    def __call__(self, *args, **kwargs): pass\n"
        "    def default(self, fn): return fn\n"
        "\n"
        "def Parameter(*args, **kwargs): return object()\n",
        encoding="utf-8",
    )


def test_tangle_cli_wheel_imports_without_native_tangle_api(tmp_path) -> None:
    wheel = _build_wheel(tmp_path)
    stubs = tmp_path / "stubs"
    _write_import_stubs(stubs)

    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata = archive.read("tangle_cli-0.0.1.dist-info/METADATA").decode()

    requires_dist = [line for line in metadata.splitlines() if line.startswith("Requires-Dist: ")]
    assert not any(name.startswith("tangle_api/") for name in names)
    assert "Requires-Dist: tangle-api==0.0.1" not in requires_dist
    assert "Requires-Dist: tangle-api==0.0.1 ; extra == 'native'" in requires_dist

    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(wheel), str(stubs)])}
    subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            "import tangle_cli; "
            "import tangle_cli.openapi.codegen; "
            "import tangle_cli.dynamic_discovery_client; "
            "import tangle_cli.cli; "
            "tangle_cli.cli.build_app(); "
            "assert not hasattr(tangle_cli, 'TangleApiClient')",
        ],
        cwd=tmp_path,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )


def test_native_wheels_provide_static_client_binding(tmp_path) -> None:
    cli_wheel = _build_wheel(tmp_path / "cli")
    api_wheel = _build_wheel(tmp_path / "api", "--package", "tangle-api")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(cli_wheel), str(api_wheel)])}

    subprocess.run(
        [
            sys.executable,
            "-c",
            "from tangle_cli.client import TangleApiClient; "
            "assert TangleApiClient.__name__ == 'TangleApiClient'",
        ],
        cwd=tmp_path,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )
