"""tangle-cli public API.

The package import is intentionally lightweight: native static API bindings live
in ``tangle_api.generated`` and may be supplied by the consumer environment.
Import ``tangle_cli.client.TangleApiClient`` explicitly when those generated
bindings are available.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as metadata_version

from tangle_cli.dynamic_discovery_client import TangleDynamicDiscoveryClient

try:
    __version__ = metadata_version("tangle-cli")
except PackageNotFoundError:
    __version__ = "0.1.0"

__all__ = ["TangleDynamicDiscoveryClient", "__version__"]
