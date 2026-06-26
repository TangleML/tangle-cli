from __future__ import annotations

import builtins
import importlib
import json
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from tangle_cli import cli, secrets_cli


def run_app(app: Any, args: list[str]) -> None:
    try:
        app(args)
    except SystemExit as exc:
        if exc.code not in (0, None):
            raise


class FakeLazyTangleApiClient:
    instances: list["FakeLazyTangleApiClient"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.calls: list[dict[str, Any]] = []
        FakeLazyTangleApiClient.instances.append(self)

    def secrets_list(self) -> SimpleNamespace:
        self.calls.append({"method": "secrets_list"})
        return SimpleNamespace(
            secrets=[
                {
                    "secret_name": "API_TOKEN",
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-02T00:00:00Z",
                    "expires_at": None,
                    "description": "token for API",
                    "secret_value": "must-not-leak",
                }
            ]
        )

    def secrets_create(
        self,
        secret_name: str,
        secret_value: str,
        *,
        description: str | None = None,
        expires_at: str | None = None,
    ) -> SimpleNamespace:
        self.calls.append(
            {
                "method": "secrets_create",
                "secret_name": secret_name,
                "secret_value": secret_value,
                "description": description,
                "expires_at": expires_at,
            }
        )
        return SimpleNamespace(
            secret_name=secret_name,
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
            expires_at=expires_at,
            description=description,
        )

    def secrets_update(
        self,
        secret_name: str,
        secret_value: str,
        *,
        description: str | None = None,
        expires_at: str | None = None,
    ) -> SimpleNamespace:
        self.calls.append(
            {
                "method": "secrets_update",
                "secret_name": secret_name,
                "secret_value": secret_value,
                "description": description,
                "expires_at": expires_at,
            }
        )
        return SimpleNamespace(
            secret_name=secret_name,
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-03T00:00:00Z",
            expires_at=expires_at,
            description=description,
        )

    def secrets_delete(self, secret_name: str) -> None:
        self.calls.append({"method": "secrets_delete", "secret_name": secret_name})


@pytest.fixture(autouse=True)
def fake_lazy_client(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeLazyTangleApiClient.instances = []
    monkeypatch.setattr(secrets_cli, "LazyTangleApiClient", FakeLazyTangleApiClient)


def test_sdk_secrets_help_lists_read_write_commands(capsys: pytest.CaptureFixture[str]) -> None:
    app = cli.build_app()

    run_app(app, ["sdk", "secrets", "--help"])

    output = capsys.readouterr().out
    assert "list" in output
    assert "create" in output
    assert "update" in output
    assert "delete" in output


def test_sdk_secrets_list_prints_metadata_without_values(capsys: pytest.CaptureFixture[str]) -> None:
    app = cli.build_app()

    run_app(app, ["sdk", "secrets", "list"])

    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result == {
        "status": "success",
        "count": 1,
        "secrets": [
            {
                "secret_name": "API_TOKEN",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-02T00:00:00Z",
                "description": "token for API",
            }
        ],
    }
    assert "must-not-leak" not in captured.out
    assert "must-not-leak" not in captured.err
    assert FakeLazyTangleApiClient.instances[0].calls == [{"method": "secrets_list"}]


def test_sdk_secrets_create_with_value_calls_generated_operation_without_leaking_value(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = cli.build_app()

    run_app(
        app,
        [
            "sdk",
            "secrets",
            "create",
            "API_TOKEN",
            "--value",
            "super-secret",
            "--description",
            "demo",
        ],
    )

    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["status"] == "success"
    assert result["action"] == "created"
    assert result["secret"]["secret_name"] == "API_TOKEN"
    assert result["secret"]["description"] == "demo"
    assert "super-secret" not in captured.out
    assert "super-secret" not in captured.err
    assert FakeLazyTangleApiClient.instances[0].calls == [
        {
            "method": "secrets_create",
            "secret_name": "API_TOKEN",
            "secret_value": "super-secret",
            "description": "demo",
            "expires_at": None,
        }
    ]


def test_sdk_secrets_create_with_from_env_resolves_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TANGLE_SECRET_VALUE", "from-env-secret")
    app = cli.build_app()

    run_app(app, ["sdk", "secrets", "create", "API_TOKEN", "--from-env", "TANGLE_SECRET_VALUE"])

    assert FakeLazyTangleApiClient.instances[0].calls[0]["secret_value"] == "from-env-secret"


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (
            ["sdk", "secrets", "create", "API_TOKEN", "--value", "secret", "--from-env", "SECRET_ENV"],
            "specify either --value or --from-env",
        ),
        (["sdk", "secrets", "create", "API_TOKEN"], "either --value or --from-env is required"),
        (
            ["sdk", "secrets", "create", "API_TOKEN", "--from-env", "MISSING_SECRET_ENV"],
            "environment variable 'MISSING_SECRET_ENV' is not set",
        ),
    ],
)
def test_sdk_secrets_create_value_validation_errors_do_not_leak_values(args: list[str], message: str) -> None:
    app = cli.build_app()

    with pytest.raises(SystemExit) as exc_info:
        app(args)

    assert message in str(exc_info.value)
    assert "secret" not in str(exc_info.value).replace("--from-env", "")
    assert not FakeLazyTangleApiClient.instances or FakeLazyTangleApiClient.instances[0].calls == []


def test_sdk_secrets_update_with_from_env_calls_generated_operation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UPDATED_SECRET", "updated-secret")
    app = cli.build_app()

    run_app(
        app,
        [
            "sdk",
            "secrets",
            "update",
            "API_TOKEN",
            "--from-env",
            "UPDATED_SECRET",
            "--expires-at",
            "2026-12-31T00:00:00Z",
        ],
    )

    assert FakeLazyTangleApiClient.instances[0].calls == [
        {
            "method": "secrets_update",
            "secret_name": "API_TOKEN",
            "secret_value": "updated-secret",
            "description": None,
            "expires_at": "2026-12-31T00:00:00Z",
        }
    ]


def test_sdk_secrets_update_rejects_missing_value() -> None:
    app = cli.build_app()

    with pytest.raises(SystemExit) as exc_info:
        app(["sdk", "secrets", "update", "API_TOKEN"])

    assert "either --value or --from-env is required" in str(exc_info.value)


def test_sdk_secrets_delete_prompts_unless_forced(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(builtins, "input", lambda: "n")
    app = cli.build_app()

    with pytest.raises(SystemExit) as exc_info:
        app(["sdk", "secrets", "delete", "API_TOKEN"])

    captured = capsys.readouterr()
    assert "Delete cancelled" in str(exc_info.value)
    assert "Are you sure you want to delete secret 'API_TOKEN'? [y/N]: " in captured.err
    assert "Are you sure" not in captured.out
    assert FakeLazyTangleApiClient.instances[0].calls == []


def test_sdk_secrets_delete_confirmed_prompt_goes_to_stderr_and_stdout_is_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(builtins, "input", lambda: "yes")
    app = cli.build_app()

    run_app(app, ["sdk", "secrets", "delete", "API_TOKEN"])

    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result == {"status": "success", "action": "deleted", "secret_name": "API_TOKEN"}
    assert "Are you sure you want to delete secret 'API_TOKEN'? [y/N]: " in captured.err
    assert "Are you sure" not in captured.out
    assert FakeLazyTangleApiClient.instances[0].calls == [
        {"method": "secrets_delete", "secret_name": "API_TOKEN"}
    ]


def test_sdk_secrets_delete_force_calls_generated_operation_without_prompt(capsys: pytest.CaptureFixture[str]) -> None:
    app = cli.build_app()

    run_app(app, ["sdk", "secrets", "delete", "API_TOKEN", "--force"])

    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result == {"status": "success", "action": "deleted", "secret_name": "API_TOKEN"}
    assert "Are you sure" not in captured.out
    assert "Are you sure" not in captured.err
    assert FakeLazyTangleApiClient.instances[0].calls == [
        {"method": "secrets_delete", "secret_name": "API_TOKEN"}
    ]


def test_sdk_secrets_config_array_and_config_base_url_credential_isolation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("SECOND_SECRET", "second-secret")
    config = tmp_path / "secrets.yaml"
    config.write_text(
        "_defaults:\n"
        "  base_url: https://config.example\n"
        "  token: config-token\n"
        "  auth_header: Bearer config-auth\n"
        "  header:\n"
        "    - 'X-Config: yes'\n"
        "configs:\n"
        "  - secret_name: FIRST_SECRET\n"
        "    value: first-secret\n"
        "    description: first\n"
        "  - secret_name: SECOND_SECRET\n"
        "    from_env: SECOND_SECRET\n"
        "    description: second\n",
        encoding="utf-8",
    )
    app = cli.build_app()

    run_app(app, ["sdk", "secrets", "create", "--config", str(config)])

    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["status"] == "success"
    assert len(result["results"]) == 2
    assert "first-secret" not in captured.out
    assert "second-secret" not in captured.out
    assert [instance.kwargs for instance in FakeLazyTangleApiClient.instances] == [
        {
            "base_url": "https://config.example",
            "token": "config-token",
            "auth_header": "Bearer config-auth",
            "header": ["X-Config: yes"],
            "include_env_credentials": False,
            "command_name": "secret commands",
        },
        {
            "base_url": "https://config.example",
            "token": "config-token",
            "auth_header": "Bearer config-auth",
            "header": ["X-Config: yes"],
            "include_env_credentials": False,
            "command_name": "secret commands",
        },
    ]
    assert [instance.calls[0]["secret_name"] for instance in FakeLazyTangleApiClient.instances] == [
        "FIRST_SECRET",
        "SECOND_SECRET",
    ]
    assert [instance.calls[0]["secret_value"] for instance in FakeLazyTangleApiClient.instances] == [
        "first-secret",
        "second-secret",
    ]


def test_sdk_secrets_cli_base_url_keeps_env_credentials(tmp_path) -> None:
    config = tmp_path / "secrets.yaml"
    config.write_text("secret_name: API_TOKEN\nvalue: secret\nbase_url: https://config.example\n", encoding="utf-8")
    app = cli.build_app()

    run_app(
        app,
        [
            "sdk",
            "secrets",
            "create",
            "--config",
            str(config),
            "--base-url",
            "https://cli.example",
        ],
    )

    assert FakeLazyTangleApiClient.instances[0].kwargs["base_url"] == "https://cli.example"
    assert FakeLazyTangleApiClient.instances[0].kwargs["include_env_credentials"] is True


@pytest.mark.parametrize("log_type", ["console", "none", "file"])
def test_sdk_secrets_log_type_option_works_without_leaking_values(
    log_type: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = cli.build_app()

    run_app(app, ["sdk", "secrets", "create", "API_TOKEN", "--value", "super-secret", "--log-type", log_type])

    captured = capsys.readouterr()
    assert "super-secret" not in captured.out
    assert "super-secret" not in captured.err
    assert json.loads(captured.out)["status"] == "success"


def test_secrets_cli_imports_without_native_api(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(sys.modules):
        if name == "tangle_cli.secrets_cli" or name.startswith("tangle_api"):
            del sys.modules[name]

    original_import = builtins.__import__

    def guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "tangle_api" or name.startswith("tangle_api."):
            raise AssertionError(f"unexpected native API import: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    module = importlib.import_module("tangle_cli.secrets_cli")

    assert module.app.name == ("secrets",)
