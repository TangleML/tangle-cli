"""Tests for ``ModuleBundler`` ordering and dependency analysis.

Focused unit tests for the topological sort introduced to fix
https://github.com/Shopify/discovery/issues/30197 (alphabetical bundle
order can execute a dependent before its dependency, breaking
module-level references like ``FOO = bbb.bar()``).
"""

from __future__ import annotations

import base64
import json
import textwrap
import zlib

from tangle_cli.module_bundler import (
    ModuleBundler,
    _import_node_targets,
    _iter_module_level_nodes,
    _module_level_dependencies,
    _topological_order,
)


def _decode(b64: str) -> dict[str, str]:
    """Mirror of the runtime injection's decompress step."""
    return json.loads(zlib.decompress(base64.b64decode(b64)))


class TestTopologicalOrder:
    def test_dependency_executes_before_dependent(self):
        """Regression test for issue #30197.

        ``aaa`` references ``bbb`` at module load time, so ``bbb`` must
        be executed first even though ``aaa`` < ``bbb`` alphabetically.
        """
        sources = {
            "aaa": "import bbb\n\nFOO = bbb.bar()\n",
            "bbb": "def bar():\n    return 'BIZ'\n",
        }

        order = _topological_order(sources)

        assert order.index("bbb") < order.index("aaa")

    def test_encode_emits_topologically_ordered_dict(self):
        """``ModuleBundler.encode`` round-trips through the same order."""
        sources = {
            "aaa": "import bbb\n\nFOO = bbb.bar()\n",
            "bbb": "def bar():\n    return 'BIZ'\n",
        }

        b64 = ModuleBundler.encode(sources)
        assert b64 is not None
        decoded = _decode(b64)

        assert list(decoded.keys()) == ["bbb", "aaa"]

    def test_independent_modules_keep_deterministic_order(self):
        """With no inter-module deps, fall back to (depth, alphabetical)."""
        sources = {
            "ccc": "X = 1\n",
            "aaa": "Y = 2\n",
            "bbb": "Z = 3\n",
        }

        order = _topological_order(sources)

        assert order == ["aaa", "bbb", "ccc"]

    def test_chain_dependency(self):
        """``a -> b -> c`` must execute as ``c, b, a``."""
        sources = {
            "a": "import b\nVAL = b.B\n",
            "b": "import c\nB = c.C\n",
            "c": "C = 42\n",
        }

        order = _topological_order(sources)

        assert order == ["c", "b", "a"]

    def test_parent_package_runs_before_submodule(self):
        """Submodules must wait for their parent package's ``__init__``."""
        sources = {
            "pkg.sub": "from pkg import THING\n",
            "pkg": "THING = 1\n",
        }

        order = _topological_order(sources)

        assert order.index("pkg") < order.index("pkg.sub")

    def test_relative_import_creates_dependency_edge(self):
        """``from . import sibling`` must order ``sibling`` before us."""
        sources = {
            "pkg": "",
            "pkg.user": "from . import helper\n\nVAL = helper.value()\n",
            "pkg.helper": "def value():\n    return 1\n",
        }

        order = _topological_order(sources)

        assert order.index("pkg.helper") < order.index("pkg.user")

    def test_parent_imports_child_does_not_create_cycle(self):
        """``from . import sibling`` *inside parent's __init__.py* is fine.

        This is a common pattern (re-exporting submodules).  An earlier
        draft of the dependency analysis added a blanket "parent before
        child" edge, which combined with the parent's relative import
        formed a cycle and silently fell back to the legacy alphabetical
        order — defeating the whole fix.  This test guards against that
        regression.
        """
        sources = {
            "mylib": "from . import helpers\n",
            "mylib.helpers": "HELP = True\n",
            "mylib.core": "def process(): pass\n",
        }

        order = _topological_order(sources)

        # helpers must execute before mylib because mylib's body imports it.
        assert order.index("mylib.helpers") < order.index("mylib")

    def test_from_pkg_sub_import_module_form(self):
        """``from pkg.sub import mod`` should depend on ``pkg.sub.mod``."""
        sources = {
            "pkg": "",
            "pkg.sub": "",
            "pkg.sub.mod": "VALUE = 7\n",
            "pkg.consumer": "from pkg.sub import mod\n\nX = mod.VALUE\n",
        }

        order = _topological_order(sources)

        assert order.index("pkg.sub.mod") < order.index("pkg.consumer")

    def test_lazy_import_does_not_create_edge(self):
        """Imports inside a function body do not constrain load order.

        Otherwise common patterns like ``def f(): import sibling`` would
        introduce spurious cycles.
        """
        sources = {
            # ``aaa`` only imports ``bbb`` lazily, so it is *not* a real
            # module-load-time dependency.  ``bbb`` imports ``aaa`` at
            # the top, so the only real edge is ``aaa -> ... `` (none) and
            # ``bbb -> aaa``.
            "aaa": "def f():\n    import bbb\n    return bbb.X\n",
            "bbb": "import aaa\n\nVAL = 1\n",
        }

        order = _topological_order(sources)

        assert order.index("aaa") < order.index("bbb")

    def test_cycle_falls_back_to_alphabetical(self):
        """Module-level cycles (which would also fail in real Python) fall back.

        The output must still be deterministic.
        """
        sources = {
            "bbb": "import aaa\n\nVAL = aaa.X\n",
            "aaa": "import bbb\n\nVAL = bbb.X\n",
        }

        order = _topological_order(sources)

        # (depth, alphabetical) fallback.
        assert order == ["aaa", "bbb"]

    def test_empty_input(self):
        assert _topological_order({}) == []
        assert ModuleBundler.encode({}) is None


class TestModuleLevelDependencies:
    def test_picks_up_top_level_import(self):
        deps = _module_level_dependencies(
            "aaa",
            "import bbb\nFOO = bbb.bar()\n",
            {"aaa", "bbb"},
        )
        assert deps == {"bbb"}

    def test_skips_imports_inside_functions(self):
        deps = _module_level_dependencies(
            "aaa",
            "def f():\n    import bbb\n    return bbb.x\n",
            {"aaa", "bbb"},
        )
        assert deps == set()

    def test_no_implicit_parent_edge(self):
        """A child without an explicit parent import has no parent edge.

        Pass 1 of the runtime injection pre-registers every module in
        ``sys.modules``, so the child only needs the parent exec'd first
        if it actually references the parent's attributes — which it
        would do via an explicit ``import`` / ``from`` statement.
        """
        deps = _module_level_dependencies(
            "pkg.sub",
            "X = 1\n",
            {"pkg", "pkg.sub"},
        )
        assert deps == set()

    def test_explicit_parent_import_creates_edge(self):
        deps = _module_level_dependencies(
            "pkg.sub",
            "from pkg import THING\n",
            {"pkg", "pkg.sub"},
        )
        assert deps == {"pkg"}

    def test_ignores_unbundled_imports(self):
        """Standard library and third-party imports are not bundle deps."""
        deps = _module_level_dependencies(
            "aaa",
            "import os\nimport pandas\n",
            {"aaa"},
        )
        assert deps == set()

    def test_handles_syntax_error_gracefully(self):
        """Unparseable source contributes no deps and never raises."""
        deps = _module_level_dependencies(
            "pkg.sub",
            "this is not valid python @@@\n",
            {"pkg", "pkg.sub"},
        )
        assert deps == set()

    def test_self_reference_excluded(self):
        """A module never depends on itself even with weird ``from`` forms."""
        deps = _module_level_dependencies(
            "aaa",
            "from aaa import x\n",  # nonsensical but shouldn't loop
            {"aaa"},
        )
        assert deps == set()


class TestImportNodeTargetsHelpers:
    def test_module_level_iterator_skips_function_bodies(self):
        import ast

        tree = ast.parse(textwrap.dedent("""\
            import top_level

            def fn():
                import nested
        """))

        names = [
            alias.name
            for node in _iter_module_level_nodes(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        ]

        assert names == ["top_level"]

    def test_relative_from_import_resolves_against_context(self):
        import ast

        node = ast.parse("from .helper import fn\n").body[0]
        targets = _import_node_targets(node, pkg_context="pkg")
        # ``fn`` may be a module or attribute; both are emitted and
        # filtered downstream by the bundled-set check.
        assert "pkg.helper" in targets
        assert "pkg.helper.fn" in targets


class TestRuntimeBundleExecution:
    """End-to-end: encode a bundle, run the injection snippet, observe.

    These tests exec the *exact* snippet shipped in the generated
    component (``ModuleBundler.build_injection``) so a regression in
    either the encode order or the runtime exec loop is caught.  No
    pre-existing test exercised this path — every earlier bundle test
    stopped at "the YAML contains ``_EMBEDDED_MODULES``".
    """

    @staticmethod
    def _exec_bundle(sources: dict[str, str], driver: str) -> dict:
        """Encode *sources*, run the injection, then run *driver*.

        Cleans up any bundled module names from ``sys.modules`` after
        execution so tests stay isolated.
        """
        import sys

        b64 = ModuleBundler.encode(sources)
        assert b64 is not None
        snippet = ModuleBundler.build_injection(b64) + "\n" + driver
        ns: dict = {}
        try:
            exec(snippet, ns)
        finally:
            for name in list(sys.modules):
                if name in sources or any(name.startswith(p + ".") for p in sources):
                    del sys.modules[name]
        return ns

    def test_issue_30197_repro_runs_correctly(self):
        """Reproduces the exact failure in issue #30197.

        Before the topological-order fix, ``aaa`` (alphabetically first)
        was exec'd before ``bbb``, so ``FOO = bbb.bar()`` raised
        ``AttributeError: module 'bbb' has no attribute 'bar'``.
        """
        sources = {
            "aaa": "import bbb\n\nFOO = bbb.bar()\n\ndef do():\n    return FOO\n",
            "bbb": "def bar():\n    return 'BIZ'\n",
        }

        ns = self._exec_bundle(sources, "import aaa\nresult = aaa.do()\n")

        assert ns["result"] == "BIZ"

    def test_chain_dependency_runs_correctly(self):
        """``a -> b -> c`` chain with module-level attribute access."""
        sources = {
            "a": "import b\n\nVAL = b.B + 1\n",
            "b": "import c\n\nB = c.C * 10\n",
            "c": "C = 4\n",
        }

        ns = self._exec_bundle(sources, "import a\nresult = a.VAL\n")

        assert ns["result"] == 41

    def test_nested_package_relative_import_runs_correctly(self):
        sources = {
            "pkg": "",
            "pkg.sub": "from . import helpers\n\nVALUE = helpers.VALUE\n",
            "pkg.sub.helpers": "VALUE = 'nested'\n",
        }

        ns = self._exec_bundle(sources, "import pkg.sub\nresult = pkg.sub.VALUE\n")

        assert ns["result"] == "nested"

    def test_parent_init_relative_import_runs_correctly(self):
        """Common pattern: parent ``__init__`` re-exports a sibling.

        Combines two patterns that earlier drafts handled poorly: a
        relative ``from . import helpers`` in the parent and a
        consumer that reaches into the helper.
        """
        sources = {
            "mylib": "from . import helpers\n\nGREETING = helpers.HELLO\n",
            "mylib.helpers": "HELLO = 'hi'\n",
        }

        ns = self._exec_bundle(sources, "import mylib\nresult = mylib.GREETING\n")

        assert ns["result"] == "hi"
