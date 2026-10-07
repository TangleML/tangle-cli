"""Bundled payloads must survive YAML/Jinja hydration before Python decodes them."""

import os
import subprocess
import sys

import pytest
import yaml

from tangle_cli.component_from_func import generate_component_yaml
from tangle_cli.module_bundler import ModuleBundler
from tangle_cli.pipeline_hydrator import PipelineHydrator
from tangle_cli.utils import dump_yaml


@pytest.mark.parametrize("mode", ["bundle", "bundle-bz2"])
@pytest.mark.parametrize(
    ("delimiter", "value"),
    [("{{", "value-510"), ("{%", "value-537"), ("{#", "value-187")],
)
def test_delimiter_bearing_bundle_survives_hydration_and_execution(tmp_path, mode, delimiter, value):
    # These small real sources produce each Jinja opening delimiter in Base85.
    # Assert the precondition so a codec change cannot silently weaken coverage.
    helper_source = f"VALUE = {value!r}\n"
    encoded = ModuleBundler.encode({"bundle_payload_helper": helper_source}, mode="bundle-bz2")
    assert encoded is not None and delimiter in encoded

    helper = tmp_path / "bundle_payload_helper.py"
    helper.write_text(helper_source, encoding="utf-8")
    component_source = tmp_path / "component.py"
    component_source.write_text(
        "from bundle_payload_helper import VALUE\n\n"
        "def report(prefix: str):\n"
        "    print(prefix + VALUE)\n",
        encoding="utf-8",
    )
    template = tmp_path / "component.yaml.j2"
    assert generate_component_yaml(
        component_source,
        template,
        container_image="python:3.12",
        function_name="report",
        mode=mode,
    )
    generated = yaml.safe_load(template.read_text(encoding="utf-8"))
    generated["name"] = "{{ component_name }}"
    template.write_text(dump_yaml(generated), encoding="utf-8")
    program = generated["implementation"]["container"]["command"][-1]
    # Follow the actual componentRef -> template_file -> render_template path,
    # rather than directly instantiating a different Jinja environment in a test.
    (tmp_path / "component-config.yaml").write_text(
        dump_yaml({"template_file": template.name, "component_name": "Rendered bundle"}),
        encoding="utf-8",
    )
    pipeline = tmp_path / "pipeline.yaml"
    pipeline.write_text(
        dump_yaml({
            "name": "Bundle hydration",
            "implementation": {"graph": {"tasks": {
                "report": {"componentRef": {"url": "file://./component-config.yaml"}},
            }}},
        }),
        encoding="utf-8",
    )
    hydrated = PipelineHydrator(error_policy="raise").hydrate_file(pipeline)
    component = hydrated.data["implementation"]["graph"]["tasks"]["report"]["componentRef"]["spec"]
    assert component["name"] == "Rendered bundle"
    assert component["implementation"]["container"]["command"][-1] == program

    # Hex escapes stay inert across another real hydration pass, unlike Jinja
    # raw blocks that disappear on the first pass.
    template.write_text(dump_yaml(component), encoding="utf-8")
    rehydrated = PipelineHydrator(error_policy="raise").hydrate_file(pipeline)
    component = rehydrated.data["implementation"]["graph"]["tasks"]["report"]["componentRef"]["spec"]
    assert component["implementation"]["container"]["command"][-1] == program

    # No source files or project import path are available to the subprocess.
    # Execute the generated sh bootstrap and argparse wrapper, not just the codec.
    helper.unlink()
    component_source.unlink()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    command = component["implementation"]["container"]["command"]
    completed = subprocess.run(
        command + ["--prefix", "hydrated:"],
        cwd=runtime,
        env={"PATH": os.pathsep.join([os.path.dirname(sys.executable), os.defpath]), "TMPDIR": str(runtime)},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == f"hydrated:{value}"
    assert all(opener not in program for opener in ("{{", "{%", "{#"))
    if mode == "bundle-bz2":
        assert r"\x7b" in program
        assert "base64.b85decode" in program
    else:
        assert "base64.b64decode" in program
        assert "import bz2" not in program
