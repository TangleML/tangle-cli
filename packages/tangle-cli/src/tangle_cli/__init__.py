"""tangle-cli public API.

The package import is intentionally lightweight: native static API bindings live
in ``tangle_api.generated`` and may be supplied by the consumer environment.
Import ``tangle_cli.client.TangleApiClient`` explicitly when those generated
bindings are available.
"""

from tangle_cli.dynamic_discovery_client import TangleDynamicDiscoveryClient

__all__ = ["TangleDynamicDiscoveryClient"]
