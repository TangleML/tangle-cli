from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

from tangle_cli.openapi import codegen


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
    _write_runtime_stubs(path)
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


def _write_runtime_stubs(path: Path) -> None:
    path.mkdir(exist_ok=True)
    (path / "httpx.py").write_text("", encoding="utf-8")
    (path / "platformdirs.py").write_text("", encoding="utf-8")
    (path / "yaml.py").write_text(
        "class ScalarNode:\n"
        "    pass\n"
        "\n"
        "class SafeDumper:\n"
        "    @classmethod\n"
        "    def add_representer(cls, *args, **kwargs): pass\n"
        "\n"
        "def add_representer(*args, **kwargs): pass\n"
        "def safe_load(*args, **kwargs): return None\n"
        "def dump(*args, **kwargs): return ''\n",
        encoding="utf-8",
    )
    (path / "requests.py").write_text(
        "class Session:\n"
        "    def request(self, *args, **kwargs):\n"
        "        raise RuntimeError('request stub should not be called')\n"
        "\n"
        "class Response:\n"
        "    pass\n",
        encoding="utf-8",
    )


def _write_consumer_tangle_api(path: Path) -> Path:
    source_root = path / "src"
    generated_dir = source_root / "tangle_api" / "generated"
    generated_dir.mkdir(parents=True)
    (source_root / "tangle_api" / "__init__.py").write_text("", encoding="utf-8")
    (generated_dir / "__init__.py").write_text("", encoding="utf-8")
    (generated_dir / "models.py").write_text(
        "class ComponentSpec:\n"
        "    source = 'consumer-local'\n"
        "    @classmethod\n"
        "    def from_dict(cls, data):\n"
        "        return cls()\n"
        "\n"
        "class GetExecutionInfoResponse:\n"
        "    source = 'consumer-local'\n"
        "    @classmethod\n"
        "    def from_dict(cls, data):\n"
        "        return cls()\n",
        encoding="utf-8",
    )
    (generated_dir / "operations.py").write_text(
        "class GeneratedTangleApiOperations:\n"
        "    def consumer_generated_marker(self):\n"
        "        return 'consumer-local-operations'\n",
        encoding="utf-8",
    )
    return source_root


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


def test_tangle_cli_wheel_binds_to_consumer_local_tangle_api(tmp_path) -> None:
    cli_wheel = _build_wheel(tmp_path / "cli")
    consumer_source = _write_consumer_tangle_api(tmp_path / "consumer")
    stubs = tmp_path / "stubs"
    _write_runtime_stubs(stubs)
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(consumer_source), str(cli_wheel), str(stubs)]),
    }

    subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            "import tangle_api.generated.models as generated_models; "
            "from tangle_cli.client import TangleApiClient; "
            "import tangle_cli.client as client_module; "
            "import tangle_cli.models as domain_models; "
            "client = TangleApiClient('https://api.test'); "
            "assert client.consumer_generated_marker() == 'consumer-local-operations'; "
            "assert client_module.ComponentSpec is generated_models.ComponentSpec; "
            "assert domain_models.ComponentSpec is generated_models.ComponentSpec; "
            "assert generated_models.ComponentSpec.source == 'consumer-local'; "
            "assert generated_models.__file__.startswith(%r)" % str(consumer_source),
        ],
        cwd=tmp_path,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )


def test_codegen_output_imports_as_consumer_local_tangle_api(tmp_path) -> None:
    source_root = tmp_path / "consumer_src"
    generated_dir = source_root / "tangle_api" / "generated"
    (source_root / "tangle_api").mkdir(parents=True)
    (source_root / "tangle_api" / "__init__.py").write_text("", encoding="utf-8")
    openapi = tmp_path / "openapi.json"
    openapi.write_text(
        json.dumps({
            "openapi": "3.1.0",
            "paths": {
                "/api/foo": {
                    "get": {
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {"$ref": "#/components/schemas/FooResponse"}
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "components": {
                "schemas": {
                    "FooResponse": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}},
                    }
                }
            },
        }),
        encoding="utf-8",
    )

    codegen.generate(openapi, generated_dir, model_extension_module="")

    env = {**os.environ, "PYTHONPATH": str(source_root)}
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; "
            "from tangle_api.generated.models import FooResponse; "
            "from tangle_api.generated.operations import GeneratedTangleApiOperations; "
            "import tangle_api.generated.models as models; "
            "assert Path(models.__file__).resolve().is_relative_to(Path(%r).resolve()); "
            "assert FooResponse.__name__ == 'FooResponse'; "
            "assert '_FooResponseGenerated' not in getattr(models, '__all__'); "
            "assert GeneratedTangleApiOperations.__name__ == 'GeneratedTangleApiOperations'"
            % str(source_root),
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
