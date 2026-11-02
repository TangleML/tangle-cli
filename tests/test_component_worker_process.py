"""Execute generated entrypoints, including their shells, without a container."""

import os
import shlex
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
import yaml

from tangle_cli.component_from_func import generate_component_yaml


pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="Generated workers use POSIX shells"
)


@pytest.fixture(params=["inline", "bundle", "bundle-bz2"])
def worker(request, tmp_path):
    source = tmp_path / "worker.py"
    source.write_text(
        textwrap.dedent("""\
        def worker(action: str, value: str, ready: str, cleanup: str):
            import os
            import signal
            import sys
            import time
            from pathlib import Path

            def stop(signum, frame):
                raise SystemExit(128 + signum)

            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            try:
                print(value, flush=True)
                print("worker stderr", file=sys.stderr, flush=True)
                Path(ready).write_text(str(os.getpid()))
                if action == "wait":
                    while True:
                        time.sleep(0.05)
                if action == "fail":
                    raise SystemExit(7)
            finally:
                Path(cleanup).write_text(value)
        """)
    )
    return source, request.param


@pytest.fixture(params=[False, True], ids=["no-dependencies", "dependencies"])
def entrypoint(request, worker, tmp_path):
    source, mode = worker
    dependencies = tmp_path / "pyproject.toml"
    dependencies.write_text(
        '[project]\nname = "test-worker"\ndependencies = ["test-dependency"]\n'
    )
    output = tmp_path / "component.yaml"
    assert generate_component_yaml(
        source,
        output,
        container_image="python:3.12",
        function_name="worker",
        mode=mode,
        dependencies_from=dependencies if request.param else None,
    )
    command = yaml.safe_load(output.read_text())["implementation"]["container"][
        "command"
    ]
    # Only stub pip's network/install step. All generated shells and the worker
    # itself run as real processes; use this test environment's Python.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    python = bin_dir / "python3"
    python.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then\n'
        '  printf "pip install\\n" >> "$PIP_CALLS"\n'
        "  exit 0\n"
        "fi\n"
        f'exec {shlex.quote(sys.executable)} "$@"\n'
    )
    python.chmod(0o755)
    env = dict(
        os.environ,
        PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        TMPDIR=str(tmp_path),
        PIP_CALLS=str(tmp_path / "pip-calls"),
    )
    return command, env, request.param


def _wait_for_file(path: Path, process: subprocess.Popen, timeout: float = 5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.read_text():
            return
        assert process.poll() is None, (
            f"Worker exited before readiness: {process.returncode}"
        )
        time.sleep(0.01)
    pytest.fail("Worker did not become ready before the deadline")


@pytest.mark.parametrize("action", ["success", "fail", "wait"])
def test_generated_worker_lifecycle(entrypoint, action, tmp_path):
    command, env, has_dependencies = entrypoint
    value = "spaces 'quotes' $dollar ; literal\nsecond line"
    ready, cleanup = tmp_path / "ready", tmp_path / "cleanup"
    args = [
        "--action",
        action,
        "--value",
        value,
        "--ready",
        str(ready),
        "--cleanup",
        str(cleanup),
    ]
    stdout, stderr = tmp_path / "stdout", tmp_path / "stderr"
    with stdout.open("w") as out, stderr.open("w") as err:
        process = subprocess.Popen(
            command + args, env=env, stdout=out, stderr=err, start_new_session=True
        )
        try:
            if action == "wait":
                _wait_for_file(ready, process)
                # Mimic a container runtime: signal ONLY the entrypoint PID,
                # not the process group (which would hide a non-exec wrapper).
                process.send_signal(signal.SIGTERM)
            returncode = process.wait(timeout=10)
            assert cleanup.exists(), "SIGTERM did not reach the Python cleanup handler"
            assert cleanup.read_text() == value
            assert (
                returncode
                == {"success": 0, "fail": 7, "wait": 128 + signal.SIGTERM}[action]
            )
            assert stdout.read_text() == value + "\n"
            assert stderr.read_text() == "worker stderr\n"
            assert (tmp_path / "pip-calls").exists() == has_dependencies
            if action == "wait":
                assert int(ready.read_text()) == process.pid
        finally:
            # Also reap an orphan worker on the red-before path or any timeout.
            # A separate session keeps cleanup from touching the test runner.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
