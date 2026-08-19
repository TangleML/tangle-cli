"""`tangle sdk secrets` command implementation."""

from __future__ import annotations

import sys
from typing import Annotated, Any, Callable

from cyclopts import App, Parameter

from .args_container import ArgsContainer
from .cli_helpers import (
    LazyTangleApiClient,
    api_arg_specs,
    include_env_credentials_for_args,
    load_args_or_exit,
    print_json,
)
from .cli_options import (
    AuthHeaderOption,
    BaseUrlOption,
    ConfigOption,
    HeaderOption,
    LogTypeOption,
    TokenOption,
)
from .logger import Logger, logger_for_log_type
from .secrets import SecretsManager, SecretValueError

ValueOption = Annotated[
    str | None,
    Parameter(
        name="--value",
        alias="-v",
        help="Secret value. Prefer --from-env to avoid shell history exposure.",
    ),
]
FromEnvOption = Annotated[
    str | None,
    Parameter(
        name="--from-env",
        alias="-e",
        help="Read secret value from this environment variable.",
    ),
]
DescriptionOption = Annotated[
    str | None,
    Parameter(name="--description", alias="-d", help="Secret description."),
]
ExpiresAtOption = Annotated[
    str | None,
    Parameter(help="Expiration datetime (ISO 8601)."),
]
ForceOption = Annotated[
    bool,
    Parameter(help="Skip confirmation prompt."),
]

app = App(name="secrets", help="Manage Tangle secrets.")


def _client(
    args: ArgsContainer, *, cli_base_url: str | None, command_name: str, logger: Logger | None = None
) -> LazyTangleApiClient:
    return LazyTangleApiClient(
        base_url=args.base_url,
        token=args.token,
        auth_header=args.auth_header,
        header=args.header,
        include_env_credentials=include_env_credentials_for_args(args, cli_base_url),
        command_name=command_name,
        logger=logger,
    )


def _run_secret_action(
    config: str | None,
    cli_base_url: str | None,
    specs: dict[str, tuple[Any, ...]],
    fn: Callable[[Any, ArgsContainer, Logger], dict[str, Any]],
) -> None:
    results: list[dict[str, Any]] = []
    for args in load_args_or_exit(config, **specs):
        logger, finalize_logs = logger_for_log_type(getattr(args, "log_type", "console"))
        try:
            client = _client(args, cli_base_url=cli_base_url, command_name="secret commands", logger=logger)
            try:
                results.append(fn(client, args, logger))
            except SecretValueError as exc:
                raise SystemExit(str(exc)) from exc
        finally:
            finalize_logs()

    print_json(results[0] if len(results) == 1 else {"status": "success", "results": results})


def _secret_mutation_specs(
    *,
    secret_name: str | None,
    value: str | None,
    from_env: str | None,
    description: str | None,
    expires_at: str | None,
    base_url: str | None,
    token: str | None,
    auth_header: str | None,
    header: list[str] | None,
    log_type: str,
) -> dict[str, tuple[Any, ...]]:
    return {
        "secret_name": ("secret_name", secret_name, None, False, True),
        "value": (value, None),
        "from_env": (from_env, None),
        "description": (description, None),
        "expires_at": (expires_at, None),
        "log_type": (log_type, "console"),
        **api_arg_specs(base_url=base_url, token=token, auth_header=auth_header, header=header),
    }


def _confirm_delete(secret_name: str) -> None:
    prompt = f"Are you sure you want to delete secret '{secret_name}'? [y/N]: "
    print(prompt, end="", file=sys.stderr, flush=True)
    try:
        response = input()
    except EOFError as exc:  # pragma: no cover - defensive for non-interactive shells
        raise SystemExit("Delete cancelled") from exc
    if response.strip().lower() not in {"y", "yes"}:
        raise SystemExit("Delete cancelled")


@app.command(name="list")
def secrets_list(
    *,
    base_url: BaseUrlOption = None,
    token: TokenOption = None,
    auth_header: AuthHeaderOption = None,
    header: HeaderOption = None,
    config: ConfigOption = None,
    log_type: LogTypeOption = "console",
) -> None:
    """List secret metadata. Secret values are never returned."""

    specs = {
        "log_type": (log_type, "console"),
        **api_arg_specs(base_url=base_url, token=token, auth_header=auth_header, header=header),
    }

    def action(client: Any, args: ArgsContainer, logger: Logger) -> dict[str, Any]:
        result = SecretsManager(client=client).list()
        logger.info(f"Listed {result['count']} secret(s).")
        return result

    _run_secret_action(config, base_url, specs, action)


@app.command(name="create")
def secrets_create(
    secret_name: str | None = None,
    *,
    value: ValueOption = None,
    from_env: FromEnvOption = None,
    description: DescriptionOption = None,
    expires_at: ExpiresAtOption = None,
    base_url: BaseUrlOption = None,
    token: TokenOption = None,
    auth_header: AuthHeaderOption = None,
    header: HeaderOption = None,
    config: ConfigOption = None,
    log_type: LogTypeOption = "console",
) -> None:
    """Create a new secret."""

    specs = _secret_mutation_specs(
        secret_name=secret_name,
        value=value,
        from_env=from_env,
        description=description,
        expires_at=expires_at,
        base_url=base_url,
        token=token,
        auth_header=auth_header,
        header=header,
        log_type=log_type,
    )

    def action(client: Any, args: ArgsContainer, logger: Logger) -> dict[str, Any]:
        result = SecretsManager(client=client).create(
            args.secret_name,
            value=args.value,
            from_env=args.from_env,
            description=args.description,
            expires_at=args.expires_at,
        )
        logger.info(f"Created secret: {args.secret_name}")
        return result

    _run_secret_action(config, base_url, specs, action)


@app.command(name="update")
def secrets_update(
    secret_name: str | None = None,
    *,
    value: ValueOption = None,
    from_env: FromEnvOption = None,
    description: DescriptionOption = None,
    expires_at: ExpiresAtOption = None,
    base_url: BaseUrlOption = None,
    token: TokenOption = None,
    auth_header: AuthHeaderOption = None,
    header: HeaderOption = None,
    config: ConfigOption = None,
    log_type: LogTypeOption = "console",
) -> None:
    """Update an existing secret."""

    specs = _secret_mutation_specs(
        secret_name=secret_name,
        value=value,
        from_env=from_env,
        description=description,
        expires_at=expires_at,
        base_url=base_url,
        token=token,
        auth_header=auth_header,
        header=header,
        log_type=log_type,
    )

    def action(client: Any, args: ArgsContainer, logger: Logger) -> dict[str, Any]:
        result = SecretsManager(client=client).update(
            args.secret_name,
            value=args.value,
            from_env=args.from_env,
            description=args.description,
            expires_at=args.expires_at,
        )
        logger.info(f"Updated secret: {args.secret_name}")
        return result

    _run_secret_action(config, base_url, specs, action)


@app.command(name="delete")
def secrets_delete(
    secret_name: str | None = None,
    *,
    force: ForceOption = False,
    base_url: BaseUrlOption = None,
    token: TokenOption = None,
    auth_header: AuthHeaderOption = None,
    header: HeaderOption = None,
    config: ConfigOption = None,
    log_type: LogTypeOption = "console",
) -> None:
    """Delete a secret. Prompts for confirmation unless ``--force`` is used."""

    specs = {
        "secret_name": ("secret_name", secret_name, None, False, True),
        "force": (force, False),
        "log_type": (log_type, "console"),
        **api_arg_specs(base_url=base_url, token=token, auth_header=auth_header, header=header),
    }

    def action(client: Any, args: ArgsContainer, logger: Logger) -> dict[str, Any]:
        if not args.force:
            _confirm_delete(args.secret_name)
        result = SecretsManager(client=client).delete(args.secret_name)
        logger.info(f"Deleted secret: {args.secret_name}")
        return result

    _run_secret_action(config, base_url, specs, action)
