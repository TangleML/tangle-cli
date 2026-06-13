"""Structured logging for tangle-cli.

Provides an injectable logger abstraction so library code never calls print()
directly.  CLI entry points use the default :class:`ConsoleLogger` (same
behaviour as bare ``print``).  Wrappers that need to capture output (an MCP
server, a test harness, etc.) inject a :class:`CaptureLogger` that
accumulates messages in memory and returns them as a single string.

CLI commands use :func:`run_with_logging` to handle the ``--log-type`` flag
uniformly::

    run_with_logging(log_type, lambda logger: my_core_func(..., logger=logger))
"""

from __future__ import annotations

import json
import sys
import tempfile
from typing import Any, Callable, Protocol


class Logger(Protocol):
    """Minimal logging protocol for Tangle tooling."""

    def info(self, msg: str) -> None: ...
    def warn(self, msg: str) -> None: ...
    def error(self, msg: str) -> None: ...


class ConsoleLogger:
    """Default logger — prints to stderr so structured output on stdout stays clean."""

    def info(self, msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)

    def warn(self, msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)

    def error(self, msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)


class CaptureLogger:
    """Logger for MCP: accumulates messages in memory.

    Use :meth:`get_logs` to retrieve the collected output as a single string.
    """

    def __init__(self) -> None:
        self._messages: list[str] = []

    def info(self, msg: str) -> None:
        self._messages.append(msg)

    def warn(self, msg: str) -> None:
        self._messages.append(msg)

    def error(self, msg: str) -> None:
        self._messages.append(f"[error] {msg}")

    def get_logs(self) -> str | None:
        """Return accumulated logs as a single string, or None if empty."""
        text = "\n".join(self._messages).strip()
        return text if text else None


class NullLogger:
    """Logger that discards all messages. Used by MCP when include_logs is False."""

    def info(self, msg: str) -> None:
        pass

    def warn(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        pass


_default_logger = ConsoleLogger()
_null_logger = NullLogger()


def get_default_logger() -> ConsoleLogger:
    """Return the module-level default :class:`ConsoleLogger`."""
    return _default_logger


# Valid log_type values for CLI commands
CliLogType = str  # Valid values: "console", "none", "file" (Literal not supported by typer)


class LogFinalizer(Protocol):
    def __call__(self) -> None: ...


def logger_for_log_type(log_type: CliLogType) -> tuple[Logger, LogFinalizer]:
    """Return a logger/finalizer pair for TD-compatible CLI log types.

    ``console`` logs to stderr, ``none`` discards logs, and ``file`` captures
    logs to a temporary file whose path is printed to stderr by the finalizer.
    Callers that need custom structured stdout handling can use this lower-level
    helper instead of :func:`run_with_logging`.
    """

    if log_type == "console":
        return _default_logger, lambda: None
    if log_type == "none":
        return _null_logger, lambda: None
    if log_type == "file":
        capture = CaptureLogger()

        def finalize() -> None:
            if logs := capture.get_logs():
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".log", prefix="tangle_", delete=False,
                ) as f:
                    f.write(logs)
                print(f"\nLogs written to: {f.name}", file=sys.stderr)

        return capture, finalize
    raise SystemExit("--log-type must be one of: console, none, file")


def _print_result(result: Any) -> None:
    """Print a function result as JSON (dicts) or plain text.

    Uses plain :func:`print` so this module has no CLI-framework
    dependency.  Concrete CLI wrappers built on top of ``tangle-cli``
    can wrap this with ``typer.echo`` / ``click.echo`` if they need
    terminal-aware encoding handling.
    """
    if result is None:
        return
    if isinstance(result, dict):
        print(json.dumps(result, indent=2, default=str))
    else:
        print(result)


def run_with_logging(
    log_type: CliLogType,
    fn: Callable[[Logger], dict[str, Any] | Any],
) -> None:
    """Run *fn* with the appropriate logger for *log_type*, then handle output.

    This is the universal CLI wrapper for the ``--log-type`` flag:

    - **console** (default): logs stream to stdout/stderr via :class:`ConsoleLogger`.
      If *fn* returns a non-None result, it is printed as JSON after the logs.
    - **none**: logs are discarded. The function result is printed as JSON.
    - **file**: logs are captured and written to a temp file whose path is
      printed to stderr. The function result is printed as JSON.

    *fn* receives a :class:`Logger` and should return a dict (or any value).
    Return ``None`` to suppress result output (useful when the logs *are* the output).
    """
    logger, finalize = logger_for_log_type(log_type)

    try:
        result = fn(logger)
        _print_result(result)
    finally:
        finalize()
