"""tangle-cli public API.

The package import is intentionally lightweight: static API bindings live in
``tangle_api.generated`` (included by default in public installs, or supplied by
source/downstream environments). Import ``tangle_cli.client.TangleApiClient``
explicitly when generated bindings should be loaded.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as metadata_version

from tangle_cli.dynamic_discovery_client import TangleDynamicDiscoveryClient

try:
    __version__ = metadata_version("tangle-cli")
except PackageNotFoundError:
    __version__ = "0.1.6"

__all__ = ["TangleDynamicDiscoveryClient", "__version__"]
