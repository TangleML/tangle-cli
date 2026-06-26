"""Tests for the tangle-cli root CLI."""

from __future__ import annotations

import pytest


def test_tangle_cli_version_command_prints_package_version(capsys) -> None:
    from tangle_cli import __version__
    from tangle_cli.cli import build_app

    with pytest.raises(SystemExit) as exc:
        build_app()(tokens=["version"], exit_on_error=False)

    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == __version__
