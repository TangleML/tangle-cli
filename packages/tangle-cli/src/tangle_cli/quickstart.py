"""Native-free quickstart text for the root ``tangle`` CLI."""

from __future__ import annotations

from textwrap import dedent

from cyclopts import App


app = App(name="quickstart", help="Print a concise native-free guide to the Tangle CLI.")


QUICKSTART_TEXT = dedent(
    """
    Tangle CLI quickstart
    =====================

    Command families
    ----------------
    tangle api ...
      Pure OpenAPI wrappers around a Tangle API. Commands are generated from
      the checked-in official schema and can be extended from a live backend
      schema cache. Use these when you want a direct backend endpoint call.

    tangle sdk ...
      Hand-written SDK commands for local workflows and compound operations.
      Some commands are local-only (for example pipeline validation/layout and
      component generation); others call the API through the generated client
      while adding domain behavior such as hydration, submit payload shaping,
      version checks, or config batching.

    Common flags and environment
    ----------------------------
    API-backed commands commonly accept:
      --base-url URL       API base URL (or TANGLE_API_URL)
      --token TOKEN        bearer token (or TANGLE_API_TOKEN)
      --auth-header VALUE  full Authorization value, e.g. 'Basic ...' or
                           'Bearer ...' (or TANGLE_API_AUTH_HEADER /
                           TANGLE_AUTH_HEADER)
      -H, --header 'N: V'  extra headers; repeatable (or TANGLE_API_HEADERS)
      --config PATH        YAML/JSON defaults; CLI values win over config
      --log-type TYPE      progress logs: console, none, file (SDK commands)

    TANGLE_VERBOSE=1 enables redacted HTTP request/response diagnostics on
    stderr. It is separate from normal progress logging and should not be
    required for routine hydration/publish progress.

    Protected API examples
    ----------------------
      tangle api refresh --base-url https://api.example \\
        --auth-header 'Bearer ...' -H 'X-Gateway-Auth: ...'

      tangle api pipeline-runs list --base-url https://api.example \\
        --auth-header 'Basic ...' -H 'X-Api-Key: ...'

      tangle sdk pipeline-runs submit pipeline.yaml --base-url https://api.example \\
        --auth-header 'Bearer ...' --log-type console

    Local SDK examples
    ------------------
      tangle sdk pipelines validate pipeline.yaml
      tangle sdk pipelines hydrate pipeline.yaml --output hydrated.yaml
      tangle sdk components generate from-python component.py --image python:3.12
      tangle sdk components bump-version component.yaml

    API-backed SDK examples
    -----------------------
      tangle sdk published-components search transformer --base-url https://api.example
      tangle sdk published-components publish component.yaml --dry-run
      tangle sdk pipeline-runs submit pipeline.yaml --dry-run --log-type none
      tangle sdk pipeline-runs status RUN_ID --base-url https://api.example

    Generated vs hand-written packages
    ----------------------------------
    tangle_cli is the hand-written package: CLI wiring, local SDK workflows,
    dynamic schema discovery, codegen, logging, hydrator/resolver logic, and
    extension hooks.

    tangle_api is the generated/native package: checked-in Pydantic models,
    endpoint operation methods, and the official OpenAPI snapshot. Local-only
    SDK commands and this quickstart do not need it. Static API-backed commands
    need tangle-cli[native] or an equivalent local tangle_api.generated package.

    Generated model extensions use private generated bases plus stable public
    subclasses, e.g. ComponentSpec(ComponentSpecExtensions,
    _ComponentSpecGenerated). Extension bases are left of the generated base in
    the MRO, and downstream --model-extension-module values can add/override
    behavior while preserving generated fields and stable names.

    Discover more
    -------------
      tangle --help
      tangle api --help
      tangle api refresh --help
      tangle sdk --help
      tangle sdk pipelines --help
      tangle sdk pipeline-runs submit --help

    See README.md for codegen/autogen instructions and extension surfaces:
    hydrator resolvers, PipelineRunHooks, ComponentPublishHook, and shared CLI
    options/logging helpers.
    """
).strip()


@app.default
def quickstart() -> None:
    """Print a concise native-free guide to the Tangle CLI."""

    print(QUICKSTART_TEXT)
