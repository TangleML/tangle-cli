"""Tests for :mod:`tangle_cli.logger`.

The most important guarantee here is that the logger module has **no
hard runtime dependency on any CLI framework** (typer / click).  It
imports only stdlib, so logger helpers keep working without any CLI-framework
imports.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap

from tangle_cli.logger import (
    CaptureLogger,
    ConsoleLogger,
    Logger,
    NullLogger,
    get_default_logger,
    run_with_logging,
)


class TestLoggers:
    def test_console_logger_writes_to_stderr(self, capsys):
        ConsoleLogger().info("hello")
        captured = capsys.readouterr()
        assert "hello" in captured.err
        assert captured.out == ""

    def test_capture_logger_accumulates_messages(self):
        cl = CaptureLogger()
        cl.info("one")
        cl.warn("two")
        cl.error("three")
        logs = cl.get_logs() or ""
        assert "one" in logs and "two" in logs and "three" in logs
        # ``error`` is tagged so callers can spot errors in collected logs.
        assert "[error]" in logs

    def test_capture_logger_get_logs_none_when_empty(self):
        assert CaptureLogger().get_logs() is None

    def test_null_logger_discards(self, capsys):
        NullLogger().info("ignored")
        NullLogger().warn("ignored")
        NullLogger().error("ignored")
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    def test_get_default_logger_returns_console_logger(self):
        assert isinstance(get_default_logger(), ConsoleLogger)


class TestRunWithLogging:
    """``run_with_logging`` must work with no third-party CLI framework installed."""

    def test_console_with_dict_result_prints_json(self, capsys):
        def fn(_logger: Logger) -> dict:
            return {"hello": "world"}

        run_with_logging("console", fn)
        captured = capsys.readouterr()
        # The dict result is serialized to JSON on stdout.
        parsed = json.loads(captured.out)
        assert parsed == {"hello": "world"}

    def test_console_with_none_result_prints_nothing_to_stdout(self, capsys):
        run_with_logging("console", lambda _logger: None)
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_console_with_logger_writes_to_stderr(self, capsys):
        def fn(logger: Logger) -> None:
            logger.info("doing the thing")
            return None

        run_with_logging("console", fn)
        captured = capsys.readouterr()
        assert "doing the thing" in captured.err
        assert captured.out == ""

    def test_none_log_type_discards_logs_but_prints_result(self, capsys):
        def fn(logger: Logger) -> dict:
            logger.info("this should be discarded")
            return {"ok": True}

        run_with_logging("none", fn)
        captured = capsys.readouterr()
        assert "discarded" not in captured.err
        assert "discarded" not in captured.out
        assert json.loads(captured.out) == {"ok": True}

    def test_string_result_prints_as_plain_text(self, capsys):
        run_with_logging("console", lambda _logger: "plain text result")
        captured = capsys.readouterr()
        assert captured.out.strip() == "plain text result"


class TestNoTyperDependency:
    """Regression guard: the logger module must not import ``typer``.

    Spawn a clean subprocess that monkeypatches ``typer`` to make the
    import fail, then exercise :func:`run_with_logging`.  If
    ``_print_result`` ever re-introduces ``import typer``, this test
    will fail loudly with ``ModuleNotFoundError``.
    """

    def test_run_with_logging_works_without_typer(self):
        script = textwrap.dedent("""
            import sys

            # Make ``import typer`` raise inside this subprocess to simulate
            # a clean ``pip install tangle-cli`` environment.  Use the
            # modern ``find_spec`` API (PEP 451); ``find_module`` /
            # ``load_module`` are deprecated since 3.4 and removed in 3.12.
            class _Blocker:
                def find_spec(self, fullname, path=None, target=None):
                    if fullname == "typer" or fullname.startswith("typer."):
                        raise ModuleNotFoundError("typer is not installed")
                    return None

            sys.meta_path.insert(0, _Blocker())

            from tangle_cli.logger import run_with_logging
            run_with_logging("console", lambda logger: {"ok": True})
        """)
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
        )
        # Subprocess must exit cleanly and print the result as JSON.
        assert result.returncode == 0, (
            f"run_with_logging crashed when typer is missing.\n"
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )
        assert '"ok": true' in result.stdout
