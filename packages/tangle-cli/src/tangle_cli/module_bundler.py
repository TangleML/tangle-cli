"""Discovers local Python modules, bundles their source, and generates injection code.

Provides ``ModuleBundler`` for embedding local dependency modules into generated
components so they are available at runtime without requiring the original
package to be installed in the container.

Also contains ``classify_imports`` — the import classification utility used by
both the component generator and the airflow converter.
"""

import ast
import base64
import importlib.util
import json
import os
import re
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path
from typing import Literal

# Paths that indicate a module is installed (not local project code).
_INSTALLED_PACKAGE_MARKERS = ("site-packages", "dist-packages")

# =============================================================================
# Import classification
# =============================================================================


def classify_imports(
    file_path: Path,
    pip_deps: list[str] | None = None,
    resolve_root: Path | None = None,
    source: str | None = None,
) -> dict[str, Literal["stdlib", "third_party", "local"]]:
    """Classify imports in a Python file as stdlib, third-party, or local.

    Args:
        file_path: Path to the Python source file
        pip_deps: List of pip dependency strings (e.g., ["pandas==2.0", "requests>=2.28"])
        resolve_root: Directory to check for local modules. Defaults to file_path.parent.
            Use this when imports resolve relative to a different root (e.g., dags_root
            for Airflow DAG files).
        source: Pre-read source text. If provided, the file is not read again.

    Returns:
        Dict mapping module names to their classification.
    """
    if source is None:
        source = file_path.read_text()
    tree = ast.parse(source)

    # Extract top-level module names from pip deps
    third_party_names: set[str] = set()
    if pip_deps:
        for dep in pip_deps:
            # Extract package name from dependency spec like "pandas==2.0.0" or "requests>=2.28"
            name = re.split(r'[><=!~\[]', dep)[0].strip().lower()
            # Normalize: pip package names use hyphens, import names use underscores
            third_party_names.add(name.replace("-", "_"))

    # Get stdlib module names
    if hasattr(sys, "stdlib_module_names"):
        stdlib_names: frozenset[str] | set[str] = sys.stdlib_module_names
    else:
        stdlib_names = set(sys.builtin_module_names)

    result: dict[str, Literal["stdlib", "third_party", "local"]] = {}
    file_dir = resolve_root or file_path.parent

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod_name = alias.name.split(".")[0]
                result[mod_name] = _classify_module(mod_name, stdlib_names, third_party_names, file_dir)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                mod_name = node.module.split(".")[0]
                result[mod_name] = _classify_module(mod_name, stdlib_names, third_party_names, file_dir)
            elif node.level > 0:
                # Relative imports are always local
                if node.module:
                    result[node.module.split(".")[0]] = "local"
                elif node.names:
                    # `from . import helpers` — module is None, names has the imports
                    for alias in node.names:
                        result[alias.name.split(".")[0]] = "local"

    return result


def _classify_module(
    mod_name: str,
    stdlib_names: frozenset[str] | set[str],
    third_party_names: set[str],
    file_dir: Path,
) -> Literal["stdlib", "third_party", "local"]:
    """Classify a single module name.

    Uses a two-pass approach:

    1. **Filesystem check** — looks for ``<mod_name>.py`` or
       ``<mod_name>/__init__.py`` directly under *file_dir*.
    2. **importlib fallback** — uses ``importlib.util.find_spec`` to locate the
       module on ``sys.path``.  If the resolved origin is *not* inside
       ``site-packages`` or ``dist-packages`` it is treated as a local module.
       This handles project layouts where local modules live in sibling
       directories (e.g. ``src/utils`` next to ``src/components``).

    Args:
        mod_name: Top-level module name (e.g., "local_modules")
        stdlib_names: Set of standard library module names
        third_party_names: Set of third-party package names
        file_dir: Directory to check for local files/packages
    """
    if mod_name in stdlib_names:
        return "stdlib"
    if mod_name.lower() in third_party_names:
        return "third_party"
    # Check if a local .py file or package exists under file_dir
    if (file_dir / f"{mod_name}.py").exists():
        return "local"
    if (file_dir / mod_name / "__init__.py").exists():
        return "local"
    # Fallback: use importlib to search sys.path for modules in sibling directories
    if _is_local_via_importlib(mod_name):
        return "local"
    # Assume third-party if we can't determine
    return "third_party"


def _is_local_via_importlib(mod_name: str) -> bool:
    """Check whether *mod_name* resolves to a local (non-installed) module.

    Returns ``True`` when ``importlib.util.find_spec`` finds the module and its
    origin path does **not** contain ``site-packages`` or ``dist-packages``.

    Note: ``find_spec`` may execute parent package ``__init__.py`` files as a
    side effect when resolving dotted names.  We catch all exceptions broadly
    so that package-init failures (``RuntimeError``, ``KeyError``, etc.) do not
    break the static generation step.
    """
    try:
        spec = importlib.util.find_spec(mod_name)
        if spec is None:
            return False
        # Namespace packages have no origin — check submodule_search_locations
        origin = spec.origin
        search_locations = spec.submodule_search_locations
        path_to_check = origin or (str(search_locations[0]) if search_locations else None)
        if not path_to_check:
            return False
        return not any(marker in path_to_check for marker in _INSTALLED_PACKAGE_MARKERS)
    except Exception:
        return False


# =============================================================================
# ModuleBundler
# =============================================================================


class ModuleBundler:
    """Discovers local Python modules, bundles their source, and generates injection code.

    Usage::

        module_sources = ModuleBundler.collect_sources(dag_file, resolve_root=dags_root)
        b64 = ModuleBundler.encode(module_sources)
        snippet = ModuleBundler.build_injection(b64)
    """

    @staticmethod
    def classify_imports(
        file_path: Path,
        pip_deps: list[str] | None = None,
        resolve_root: Path | None = None,
    ) -> dict[str, Literal["stdlib", "third_party", "local"]]:
        """Classify imports in a Python file as stdlib, third-party, or local.

        Args:
            file_path: Path to the Python source file
            pip_deps: List of pip dependency strings (e.g., ["pandas==2.0", "requests>=2.28"])
            resolve_root: Directory to check for local modules. Defaults to file_path.parent.

        Returns:
            Dict mapping module names to their classification.
        """
        return classify_imports(file_path, pip_deps, resolve_root)

    @staticmethod
    def collect_sources(
        file_path: Path,
        resolve_root: Path | None = None,
        pip_deps: list[str] | None = None,
        source: str | None = None,
    ) -> dict[str, str]:
        """Collect source text of local dependency modules from disk.

        Resolves local imports via AST analysis and filesystem lookup, without
        requiring modules to be loaded in ``sys.modules``.

        For each local import found by ``classify_imports``, the function resolves the
        full dotted module path to a ``.py`` file (or ``__init__.py`` package) under
        *resolve_root* and reads its source text.  Transitive local imports within
        each discovered module are also collected recursively.

        Args:
            file_path: Python source file whose imports to analyse.
            resolve_root: Root directory for local module resolution.  Defaults to
                ``file_path.parent``.
            pip_deps: Pip dependency strings passed through to ``classify_imports``.
            source: Source text to analyse instead of reading *file_path*.  When
                provided, only imports present in this text are considered.  This
                is useful for scoping the bundle to a specific callable rather
                than the entire file.

        Returns:
            ``{dotted_module_name: source_text}`` for every discovered local module.
        """
        root = resolve_root or file_path.parent
        if source is None:
            source = file_path.read_text()
        classifications = classify_imports(file_path, pip_deps, resolve_root=root, source=source)

        local_top_names = {name for name, cls in classifications.items() if cls == "local"}
        if not local_top_names:
            return {}

        # Walk the AST to collect full dotted module paths (classify_imports only
        # records top-level names, e.g. "local_modules" from "from local_modules.dw import X").
        full_module_paths = _collect_full_module_paths(source, local_top_names)

        # Resolve each module path to a source file and read it
        result: dict[str, str] = {}
        visited: set[str] = set()
        _resolve_modules_recursive(full_module_paths, root, result, visited, pip_deps)
        return result

    @staticmethod
    def encode(module_sources: dict[str, str]) -> str | None:
        """Compress and base64-encode a dict of module sources for embedding.

        Modules are sorted so that dependencies execute before dependents.
        We perform a topological sort over the module-level import graph
        between bundled modules, with parent packages preceding their
        submodules.  This ensures references made *at module load time*
        (e.g. ``FOO = bbb.bar()`` at the top of ``aaa.py``) find their
        target already executed — sorting purely by depth + name fails
        whenever a dependent sorts before its dependency (issue #30197).

        If the dependency graph contains a cycle (which would also fail
        under a normal Python import for any module-level reference), we
        fall back to ``(depth, alphabetical)`` order so output stays
        deterministic.

        Args:
            module_sources: ``{module_name: source_text}`` dict.

        Returns:
            Base64-encoded string, or ``None`` if *module_sources* is empty.
        """
        if not module_sources:
            return None
        import zlib
        ordered_names = _topological_order(module_sources)
        ordered = {name: module_sources[name] for name in ordered_names}
        sources_json = json.dumps(ordered)
        compressed = zlib.compress(sources_json.encode(), level=9)
        return base64.b64encode(compressed).decode("ascii")

    @staticmethod
    def build_injection(bundled_modules_b64: str) -> str:
        """Return a Python snippet that decodes and injects bundled modules into ``sys.modules``.

        The snippet is self-contained: it imports ``sys``, ``types``, ``base64``,
        ``json``, and ``zlib``, then decompresses the embedded blob and registers
        each module via ``types.ModuleType`` + ``exec``.

        Args:
            bundled_modules_b64: Base64 string produced by ``encode``.
        """
        return textwrap.dedent(f"""\
            # --- Inject local dependency modules from embedded source ---
            import sys
            import types
            import base64
            import json
            import zlib

            _EMBEDDED_MODULES = json.loads(zlib.decompress(base64.b64decode({repr(bundled_modules_b64)})))
            # Pass 1: register all modules in sys.modules (without executing source)
            # so transitive imports between bundled modules can resolve in any order.
            _module_objs = {{}}
            _package_names = set()
            for _mod_name in _EMBEDDED_MODULES:
                _parts = _mod_name.split('.')
                for _i in range(1, len(_parts)):
                    _package_names.add('.'.join(_parts[:_i]))
            for _mod_name in _EMBEDDED_MODULES:
                _parts = _mod_name.split('.')
                for _i in range(1, len(_parts)):
                    _parent = '.'.join(_parts[:_i])
                    if _parent not in sys.modules:
                        _pkg = types.ModuleType(_parent)
                        _pkg.__path__ = []
                        _pkg.__package__ = _parent
                        sys.modules[_parent] = _pkg
                _mod = sys.modules.get(_mod_name)
                if _mod is None or _mod_name not in _package_names:
                    _mod = types.ModuleType(_mod_name)
                    sys.modules[_mod_name] = _mod
                _is_package = _mod_name in _package_names
                _mod.__package__ = _mod_name if _is_package else ('.'.join(_parts[:-1]) if len(_parts) > 1 else '')
                if _is_package:
                    _mod.__path__ = []
                if len(_parts) > 1:
                    setattr(sys.modules['.'.join(_parts[:-1])], _parts[-1], _mod)
                _module_objs[_mod_name] = _mod
            # Pass 2: execute source in all registered modules
            for _mod_name, _mod_source in _EMBEDDED_MODULES.items():
                _code = compile(_mod_source, _mod_name.replace('.', '/') + '.py', 'exec')
                exec(_code, _module_objs[_mod_name].__dict__)""")


# =============================================================================
# Private helpers
# =============================================================================


def _topological_order(module_sources: dict[str, str]) -> list[str]:
    """Return bundled module names sorted so dependencies precede dependents.

    Builds a graph of module-level imports between bundled modules and
    runs ``graphlib.TopologicalSorter``.  Falls back to ``(depth,
    alphabetical)`` ordering when the graph contains a cycle so output
    remains deterministic.
    """
    from graphlib import CycleError, TopologicalSorter

    bundled = set(module_sources)
    # Insert nodes in a deterministic order so the topological sort's
    # tie-breaking (insertion order, when multiple nodes are ready) is
    # stable across runs.
    fallback_order = sorted(bundled, key=lambda n: (n.count("."), n))
    graph: dict[str, set[str]] = {name: set() for name in fallback_order}
    for name in fallback_order:
        graph[name] = _module_level_dependencies(name, module_sources[name], bundled)

    try:
        return list(TopologicalSorter(graph).static_order())
    except CycleError:
        return fallback_order


def _module_level_dependencies(name: str, source: str, bundled: set[str]) -> set[str]:
    """Return bundled modules that *name* depends on at module load time.

    Considers only imports that execute when the module is first run
    (i.e. excludes imports nested inside function or lambda bodies).

    Note we deliberately do *not* add a blanket "parent package before
    child module" edge.  Pass 1 of the runtime injection registers every
    bundled module in ``sys.modules`` up front, so a child can resolve
    ``import <parent>`` regardless of execution order.  A child only
    needs its parent exec'd first if it references the parent's
    attributes at module load — and that case shows up as an explicit
    ``from <parent> import ...`` / ``import <parent>`` in the child's
    source, which is captured below.  Adding a blanket parent-before-
    child edge would also create a spurious cycle whenever the parent's
    ``__init__.py`` does ``from . import sibling`` (a common pattern),
    forcing the topological sort to fall back to the legacy alphabetical
    order — the very behavior this function exists to replace.
    """
    deps: set[str] = set()

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return deps

    # Mirrors the package-context convention used elsewhere in the
    # bundler (see ``_resolve_modules_recursive``): top-level modules
    # use themselves as the package context, submodules use their
    # immediate parent.
    parts = name.split(".")
    pkg_context = ".".join(parts[:-1]) if len(parts) > 1 else name

    for node in _iter_module_level_nodes(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        for target in _import_node_targets(node, pkg_context):
            # Match the longest dotted prefix that is bundled — handles
            # ``from pkg.sub import mod`` where ``pkg.sub.mod`` is the
            # bundled submodule.
            tparts = target.split(".")
            for j in range(len(tparts), 0, -1):
                candidate = ".".join(tparts[:j])
                if candidate in bundled and candidate != name:
                    deps.add(candidate)
                    break
    return deps


def _iter_module_level_nodes(tree: ast.AST) -> Iterator[ast.AST]:
    """Yield AST nodes that execute at module load time.

    Skips function and lambda bodies — imports inside those only run
    when the function is called, so they do not constrain the order in
    which bundled modules must be executed.  Class bodies and
    ``if``/``try``/``with`` statements at module scope *are* executed
    at module load time and are walked normally.
    """
    if isinstance(tree, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        return
    yield tree
    for child in ast.iter_child_nodes(tree):
        yield from _iter_module_level_nodes(child)


def _import_node_targets(
    node: ast.Import | ast.ImportFrom, pkg_context: str,
) -> list[str]:
    """Return the dotted module paths an import node refers to.

    For ``from pkg import a, b`` we return ``pkg``, ``pkg.a``, and
    ``pkg.b`` — names that turn out to be attributes (not submodules)
    are filtered out by the caller via the ``bundled`` membership check.
    Relative imports are resolved against *pkg_context* using the same
    convention as ``_collect_full_module_paths``.
    """
    targets: list[str] = []
    if isinstance(node, ast.Import):
        for alias in node.names:
            targets.append(alias.name)
    elif isinstance(node, ast.ImportFrom):
        if node.module and node.level == 0:
            targets.append(node.module)
            for alias in node.names:
                targets.append(f"{node.module}.{alias.name}")
        elif node.level > 0:
            if pkg_context:
                ctx_parts = pkg_context.split(".")
                base = ".".join(ctx_parts[: max(0, len(ctx_parts) - (node.level - 1))])
                if node.module:
                    resolved = f"{base}.{node.module}" if base else node.module
                    targets.append(resolved)
                    for alias in node.names:
                        targets.append(f"{resolved}.{alias.name}")
                else:
                    for alias in node.names:
                        targets.append(f"{base}.{alias.name}" if base else alias.name)
            elif node.module:
                targets.append(node.module)
    return targets


def _collect_full_module_paths(
    source: str, local_top_names: set[str], package_context: str = "",
) -> set[str]:
    """Extract full dotted module paths for imports whose top-level name is local.

    Args:
        source: Python source code to scan.
        local_top_names: Set of top-level module names classified as local.
        package_context: Dotted package name of the module being scanned.
            Used to resolve relative imports (e.g., ``from .defaults import X``
            inside ``local_helpers.config`` becomes ``local_helpers.defaults``).
    """
    tree = ast.parse(source)
    paths: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in local_top_names:
                    paths.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                top = node.module.split(".")[0]
                if top in local_top_names:
                    paths.add(node.module)
                    # Also add child paths for each imported name — if the
                    # imported name is a submodule (e.g. `from pkg.sub import
                    # mod` where `pkg/sub/mod.py` exists), it needs to be
                    # bundled too.  Non-module names (functions, classes) will
                    # simply fail to resolve later and be ignored.
                    for alias in node.names:
                        paths.add(f"{node.module}.{alias.name}")
            elif node.level > 0:
                # Relative import — resolve to absolute path using package context.
                # Relative imports are always local by definition, so no need to
                # check against local_top_names.
                if package_context:
                    # Go up `level` packages from the current package
                    parts = package_context.split(".")
                    base = ".".join(parts[: max(0, len(parts) - (node.level - 1))])
                    if node.module:
                        resolved = f"{base}.{node.module}" if base else node.module
                        paths.add(resolved)
                        # Also add child paths for imported names (submodule case)
                        for alias in node.names:
                            paths.add(f"{resolved}.{alias.name}")
                    else:
                        # `from . import X` — import names are the modules
                        for alias in node.names:
                            paths.add(f"{base}.{alias.name}" if base else alias.name)
                elif node.module:
                    # No package context — fall back to recording verbatim
                    top = node.module.split(".")[0]
                    if top in local_top_names:
                        paths.add(node.module)
                        for alias in node.names:
                            paths.add(f"{node.module}.{alias.name}")
    return paths


def _resolve_module_file(dotted_name: str, root: Path) -> Path | None:
    """Resolve a dotted module name to a source file under *root*.

    Checks (in order):
    1. ``root/a/b/c.py``  (module)
    2. ``root/a/b/c/__init__.py``  (package)
    3. ``importlib.util.find_spec`` fallback — resolves modules on ``sys.path``
       that live outside *root* (e.g. sibling directories).  Only non-installed
       (non-``site-packages``) modules are accepted, and when *root* is an
       explicit ``resolve_root`` the resolved path must share a common project
       ancestor with *root* to prevent bundling code from unrelated projects.
    """
    parts = dotted_name.replace(".", "/")
    candidate = root / (parts + ".py")
    if candidate.exists():
        return candidate
    candidate = root / parts / "__init__.py"
    if candidate.exists():
        return candidate
    # Fallback: use importlib to find modules in sibling directories
    return _resolve_module_file_via_importlib(dotted_name, root)


def _resolve_module_file_via_importlib(dotted_name: str, root: Path) -> Path | None:
    """Resolve a dotted module name to a Python **source** file via ``importlib``.

    Returns the file path only when the module is *not* installed in
    ``site-packages`` / ``dist-packages`` (i.e. it is a local project module),
    the origin is a ``.py`` file, and the resolved path shares a common
    ancestor with *root* (i.e. lives in the same project tree).  Extension
    modules (``.so``, ``.pyd``) are excluded because the bundler reads source
    text via ``read_text()``.

    Note: ``find_spec`` may execute parent package ``__init__.py`` files as a
    side effect when resolving dotted names.  We catch all exceptions broadly
    so that package-init failures do not break the static generation step.
    """
    try:
        spec = importlib.util.find_spec(dotted_name)
        if spec is None:
            return None
        origin = spec.origin
        if not origin or origin == "frozen":
            return None
        # Only accept Python source files — extension modules (.so, .pyd)
        # cannot be read as text and must not be bundled.
        if not origin.endswith(".py"):
            return None
        origin_path = Path(origin).resolve()
        if not origin_path.exists():
            return None
        origin_str = str(origin_path)
        if any(marker in origin_str for marker in _INSTALLED_PACKAGE_MARKERS):
            return None
        # Guard: the resolved file must live under the same project tree as
        # root.  We check that root and origin share a meaningful common
        # ancestor (more specific than just "/" or a drive letter) to prevent
        # silently bundling code from unrelated projects on sys.path.
        resolved_root = root.resolve()
        try:
            # If origin is under root, great — always accept.
            origin_path.relative_to(resolved_root)
        except ValueError:
            # Origin is outside root.  Accept only if they share a common
            # ancestor that is at least 2 levels deep (e.g. /Users/me/project,
            # not just / or /Users).
            common = Path(os.path.commonpath([resolved_root, origin_path]))
            if len(common.parts) <= 2:
                return None
        return origin_path
    except Exception:
        return None


def _resolve_modules_recursive(
    module_paths: set[str],
    root: Path,
    result: dict[str, str],
    visited: set[str],
    pip_deps: list[str] | None,
) -> None:
    """Resolve module paths to source text, following transitive local imports."""
    for dotted in sorted(module_paths):
        if dotted in visited:
            continue
        visited.add(dotted)

        mod_file = _resolve_module_file(dotted, root)
        if not mod_file:
            # Also try the top-level name (package __init__)
            top = dotted.split(".")[0]
            if top not in visited:
                visited.add(top)
                pkg_init = _resolve_module_file(top, root)
                if pkg_init:
                    result[top] = pkg_init.read_text()
            continue

        mod_source = mod_file.read_text()
        result[dotted] = mod_source

        # Ensure all parent packages are collected (e.g. for "a.b.c",
        # collect "a" and "a.b" __init__.py files).  Python always
        # populates parent packages during import resolution, so the
        # bundle must include them for runtime correctness.
        # We also follow transitive imports in each parent __init__.py,
        # since Python executes them at import time and they may pull in
        # sibling modules (e.g. ``from . import helpers``).
        parts = dotted.split(".")
        for i in range(1, len(parts)):
            parent = ".".join(parts[:i])
            if parent not in visited:
                visited.add(parent)
                parent_file = _resolve_module_file(parent, root)
                if parent_file:
                    parent_source = parent_file.read_text()
                    result[parent] = parent_source
                    # Follow transitive local imports within the parent __init__.py
                    try:
                        parent_classifications = classify_imports(
                            parent_file, pip_deps, resolve_root=root, source=parent_source,
                        )
                        parent_local = {name for name, cls in parent_classifications.items() if cls == "local"}
                        if parent_local:
                            parent_paths = _collect_full_module_paths(
                                parent_source, parent_local, package_context=parent,
                            )
                            _resolve_modules_recursive(parent_paths, root, result, visited, pip_deps)
                    except Exception:
                        pass  # Best-effort transitive resolution

        # Follow transitive local imports within this module.
        # Derive the package context so relative imports resolve correctly:
        # e.g., module "local_helpers.config" has package context "local_helpers"
        parts = dotted.split(".")
        pkg_context = ".".join(parts[:-1]) if len(parts) > 1 else dotted
        try:
            child_classifications = classify_imports(mod_file, pip_deps, resolve_root=root, source=mod_source)
            child_local = {name for name, cls in child_classifications.items() if cls == "local"}
            if child_local:
                child_paths = _collect_full_module_paths(mod_source, child_local, package_context=pkg_context)
                _resolve_modules_recursive(child_paths, root, result, visited, pip_deps)
        except Exception:
            pass  # Best-effort transitive resolution
