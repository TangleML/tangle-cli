"""The ``tangle api`` command group.

The commands are generated dynamically from the backend's FastAPI routes, so
they always match the running server's API. See
:mod:`tangle_cli.api.cli_generator` for details.
"""

from .api import cli_generator

app = cli_generator.build_api_cli_app(name="api")
