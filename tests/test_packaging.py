from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

from packaging.specifiers import SpecifierSet
from packaging.version import Version

from tangle_cli.openapi import codegen

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _build_wheel(tmp_path: Path, *args: str) -> Path:
    out_dir = tmp_path / "dist"
    command = ["uv", "build", "--wheel", "--out-dir", str(out_dir), *args]
    subprocess.run(command, cwd=_REPO_ROOT, check=True, text=True, capture_output=True)
    wheels = sorted(out_dir.glob("*.whl"))
    assert wheels, f"no wheel built by {' '.join(command)}"
    return wheels[-1]


def _build_sdist(tmp_path: Path, *args: str) -> Path:
    out_dir = tmp_path / "dist"
    command = ["uv", "build", "--sdist", "--out-dir", str(out_dir), *args]
    subprocess.run(command, cwd=_REPO_ROOT, check=True, text=True, capture_output=True)
    sdists = sorted(out_dir.glob("*.tar.gz"))
    assert sdists, f"no sdist built by {' '.join(command)}"
    return sdists[-1]


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


def test_tangent_skill_bundle_is_in_repo_and_current() -> None:
    skill_root = _REPO_ROOT / "skills" / "tangent"
    expected = [
        "SKILL.md",
        "OSS-CONVENTIONS.md",
        "PORT-README.md",
        "agents/auth-wizard.md",
        "agents/builder.md",
        "agents/debugger.md",
        "agents/reporter.md",
        "agents/researcher.md",
        "agents/reviewer.md",
        "agents/scenario-builder.md",
        "references/tangle-tools.md",
        "references/setup.md",
        "references/step-0-initialize.md",
        "references/step-1-analyze.md",
        "references/step-2-hypothesize.md",
        "references/step-3-submit.md",
        "references/step-4-monitor.md",
        "references/step-5-evaluate.md",
        "references/step-6-synthesize.md",
        "references/step-7-decide.md",
    ]
    for relative in expected:
        assert (skill_root / relative).is_file(), relative

    markdown = "\n".join(path.read_text(encoding="utf-8") for path in skill_root.rglob("*.md"))
    assert "tangle-cli-lab" not in markdown
    assert "not yet a public PyPI package" not in markdown
    assert "pip install 'tangle-cli[native]'" not in markdown
    assert "uv install" not in markdown
    assert "Run commands as `tangle …` from a checkout" not in markdown
    assert "Published-package usage is the default" in markdown
    assert "uv tool install tangle-cli" in markdown
    assert "uvx --from tangle-cli tangle" in markdown
    assert "installed-tool form" in markdown
    assert "uv pip install tangle-cli` only inside an explicitly managed virtualenv" in markdown
    assert "pip install tangle-cli" in markdown
    assert "compatibility/no-op" in markdown


def test_tangle_cli_sdist_includes_tangent_skill_bundle(tmp_path) -> None:
    sdist = _build_sdist(tmp_path)
    with tarfile.open(sdist) as archive:
        names = archive.getnames()

    assert any(name.endswith("/skills/tangent/SKILL.md") for name in names)
    assert any(name.endswith("/skills/tangent/OSS-CONVENTIONS.md") for name in names)
    assert any(name.endswith("/skills/tangent/references/tangle-tools.md") for name in names)


def test_tangle_cli_wheel_supports_expert_no_deps_import_path_without_tangle_api(tmp_path) -> None:
    wheel = _build_wheel(tmp_path)
    stubs = tmp_path / "stubs"
    _write_import_stubs(stubs)

    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = archive.read(metadata_name).decode()
        entry_points_name = next(name for name in names if name.endswith(".dist-info/entry_points.txt"))
        entry_points = archive.read(entry_points_name).decode()

    requires_dist = [line for line in metadata.splitlines() if line.startswith("Requires-Dist: ")]
    assert not any(name.startswith("tangle_api/") for name in names)
    assert "tangle_cli/openapi/openapi.json" not in names
    assert "Version: 0.1.7" in metadata
    assert "Requires-Dist: tangle-api==0.1.1" in requires_dist
    assert not any("extra == 'native'" in line for line in requires_dist)
    assert "Provides-Extra: native" in metadata
    assert "tangle = tangle_cli.cli:main" in entry_points
    assert "tangle-cli = tangle_cli.cli:main" in entry_points

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


def test_custom_tangle_api_local_version_can_satisfy_cli_pin() -> None:
    assert Version("0.1.1+yourorg") in SpecifierSet("==0.1.1")


def test_tangle_cli_wheel_api_refresh_builds_in_expert_no_deps_fallback(tmp_path) -> None:
    wheel = _build_wheel(tmp_path)
    stubs = tmp_path / "stubs"
    _write_import_stubs(stubs)
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(wheel), str(stubs)])}

    subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            "import importlib.util; "
            "import sys; "
            "assert importlib.util.find_spec('tangle_api') is None; "
            "sys.argv = ['tangle', 'api', 'refresh']; "
            "import tangle_cli.cli; "
            "tangle_cli.cli.build_app()",
        ],
        cwd=tmp_path,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )


def test_tangle_cli_wheel_binds_to_project_local_tangle_api_before_official_package(tmp_path) -> None:
    cli_wheel = _build_wheel(tmp_path / "cli")
    api_wheel = _build_wheel(tmp_path / "api", "--package", "tangle-api")
    consumer_source = _write_consumer_tangle_api(tmp_path / "consumer")
    stubs = tmp_path / "stubs"
    _write_runtime_stubs(stubs)
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(consumer_source), str(cli_wheel), str(api_wheel), str(stubs)]),
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
            "assert client_module.ComponentSpec is domain_models.ComponentSpec; "
            "assert issubclass(domain_models.ComponentSpec, generated_models.ComponentSpec); "
            "assert domain_models.ComponentSpec.source == 'consumer-local'; "
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

    codegen.generate(openapi, generated_dir)

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


def test_tangle_api_source_has_no_tangle_cli_imports() -> None:
    source_root = _REPO_ROOT / "packages" / "tangle-api" / "src" / "tangle_api"
    for source in source_root.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported = [node.module or ""]
            else:
                continue
            assert not any(
                name == "tangle_cli" or name.startswith("tangle_cli.")
                for name in imported
            ), f"{source} imports {imported}"


def test_tangle_api_wheel_metadata_and_import_are_leaf(tmp_path) -> None:
    api_wheel = _build_wheel(tmp_path / "api", "--package", "tangle-api")
    with zipfile.ZipFile(api_wheel) as archive:
        metadata_name = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        metadata = archive.read(metadata_name).decode()

    requires_dist = [line for line in metadata.splitlines() if line.startswith("Requires-Dist: ")]
    assert "Requires-Dist: pydantic>=2.0" in requires_dist
    assert not any("tangle-cli" in line for line in requires_dist)

    env = {**os.environ, "PYTHONPATH": str(api_wheel)}
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import importlib.abc\n"
            "import sys\n"
            "class BlockTangleCli(importlib.abc.MetaPathFinder):\n"
            "    def find_spec(self, fullname, path=None, target=None):\n"
            "        if fullname == 'tangle_cli' or fullname.startswith('tangle_cli.'):\n"
            "            raise ModuleNotFoundError('blocked tangle_cli import')\n"
            "        return None\n"
            "sys.meta_path.insert(0, BlockTangleCli()); "
            "import tangle_api.generated.models as models; "
            "assert models.ComponentSpec.__name__ == 'ComponentSpec'; "
            "assert not any(name == 'tangle_cli' or name.startswith('tangle_cli.') for name in sys.modules)",
        ],
        cwd=tmp_path,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )


def test_default_wheels_provide_static_client_binding(tmp_path) -> None:
    cli_wheel = _build_wheel(tmp_path / "cli")
    api_wheel = _build_wheel(tmp_path / "api", "--package", "tangle-api")
    with zipfile.ZipFile(api_wheel) as archive:
        names = archive.namelist()
        assert "tangle_api/schema/__init__.py" in names
        assert "tangle_api/schema/openapi.json" in names
        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = archive.read(metadata_name).decode()

    requires_dist = [line for line in metadata.splitlines() if line.startswith("Requires-Dist: ")]
    assert "Version: 0.1.1" in metadata
    assert "Requires-Dist: pydantic>=2.0" in requires_dist
    assert not any("tangle-cli" in line for line in requires_dist)
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(cli_wheel), str(api_wheel)])}

    subprocess.run(
        [
            sys.executable,
            "-c",
            "from tangle_cli.client import TangleApiClient; "
            "from tangle_cli.openapi.parser import load_openapi_schema; "
            "assert TangleApiClient.__name__ == 'TangleApiClient'; "
            "assert 'paths' in load_openapi_schema()",
        ],
        cwd=tmp_path,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )
