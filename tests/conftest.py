"""Shared test fixtures for the tangle-cli suite.

The codegen/strip tests exercise the case where a pipeline author imported the
authoring surface from a *downstream* package's module path (e.g.
``from tangle_deploy.python_pipeline import task``). OSS recognises only its own
``tangle_cli.python_pipeline`` path out of the box; a downstream package
contributes its path through the ``register_authoring_import_module`` seam and
makes that module importable itself. This conftest stands in for that downstream
package for the whole suite — registering the path and installing a stand-in
module in ``sys.modules`` — so those tests run without OSS ever hardcoding the
downstream name, and with the process-global registry + ``sys.modules`` kept
isolated between tests.
"""

from __future__ import annotations

import sys
import types

import pytest

import tangle_cli.component_from_func as cff
from tangle_cli.component_from_func import register_authoring_import_module

# The downstream authoring module path this suite simulates. Kept out of the OSS
# source itself (that would defeat the dependency inversion) — it lives only in
# the test harness that plays the downstream package.
DOWNSTREAM_AUTHORING_MODULE = "tangle_deploy.python_pipeline"


def _install_fake_authoring_module(module: str) -> None:
    """Fabricate a stand-in downstream authoring module in ``sys.modules``.

    Mirrors what a downstream package makes importable at runtime: a module
    exposing the authoring decorators/markers so a file doing ``from <module>
    import task`` can be introspected by ``load_python_module``. OSS no longer
    fabricates this itself — that coupling was inverted into the registry seam —
    so the test harness owns it, standing in for the downstream package.
    """
    if module in sys.modules:
        return

    def _identity_decorator(*args, **kwargs):
        def decorate(func):
            return func

        return decorate

    class _AuthoringGeneric:
        def __class_getitem__(cls, item):
            return cls

        def __init__(self, *args, **kwargs):
            pass

    parent_name, _, leaf_name = module.rpartition(".")
    leaf = types.ModuleType(module)
    for name in ("task", "pipeline", "subpipeline", "registered"):
        setattr(leaf, name, _identity_decorator)
    for name in ("In", "Out", "Outputs", "TaskEnv"):
        setattr(leaf, name, _AuthoringGeneric)
    leaf.ref = lambda *args, **kwargs: None

    parent = sys.modules.get(parent_name) or types.ModuleType(parent_name)
    setattr(parent, leaf_name, leaf)
    sys.modules.setdefault(parent_name, parent)
    sys.modules[module] = leaf


@pytest.fixture(autouse=True)
def downstream_authoring_surface():
    """Register + provide a downstream authoring surface for each test.

    Codegen recognises only ``tangle_cli.python_pipeline`` out of the box; the
    legacy ``tangle_deploy.python_pipeline`` path is contributed by the
    downstream package via ``register_authoring_import_module``. The strip and
    codegen tests rely on that path being both registered (so it is stripped
    from baked source) and importable (so ``load_python_module`` can load
    fixtures that import it), so we set it up mimicking the downstream package
    and tear it down to keep the process-global registry + ``sys.modules``
    isolated between tests.
    """
    before = list(cff._AUTHORING_IMPORT_MODULES)
    parent_name = DOWNSTREAM_AUTHORING_MODULE.rpartition(".")[0]
    had_parent = parent_name in sys.modules
    had_leaf = DOWNSTREAM_AUTHORING_MODULE in sys.modules

    register_authoring_import_module(DOWNSTREAM_AUTHORING_MODULE)
    _install_fake_authoring_module(DOWNSTREAM_AUTHORING_MODULE)
    try:
        yield
    finally:
        cff._AUTHORING_IMPORT_MODULES[:] = before
        if not had_leaf:
            sys.modules.pop(DOWNSTREAM_AUTHORING_MODULE, None)
        if not had_parent:
            sys.modules.pop(parent_name, None)
