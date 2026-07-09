"""
Component YAML generator from Python functions.

Converts Python functions into Tangle component YAML files. Supports two modes:

- **inline** (default): Single-file components with source code embedded directly.
- **bundle**: Multi-file components with local dependency modules serialized via
  zlib-compressed source text and injected into sys.modules at runtime.

Key functions:
- generate_component_yaml() - Top-level entry point for YAML generation
- extract_interface() - Introspects a function's signature, types, and docstring
- extract_file_metadata() - Extracts metadata (name, version, etc.) from source via AST
- extract_docstring_metadata() - Parses the Metadata section from a docstring string
"""

import ast
import importlib.util
import inspect
import json
import os
import re
import sys
import textwrap
import types
import typing
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

import docstring_parser

from tangle_cli.module_bundler import ModuleBundler
from tangle_cli.utils import dump_yaml, get_git_info, get_git_root

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]


# ============================================================================
# InputPath / OutputPath annotation types
# ============================================================================
# These mirror the cloud_pipelines.components types so we can introspect
# functions that use them without requiring the cloud_pipelines SDK.


class InputPath:
    """Annotation indicating a function parameter receives a file path for input data."""

    def __init__(self, type: str | None = None):
        self.type = type


class OutputPath:
    """Annotation indicating a function parameter receives a file path for output data."""

    def __init__(self, type: str | None = None):
        self.type = type


# ============================================================================
# Type mapping (replicating Cloud-Pipelines SDK _data_passing.py)
# ============================================================================

# Python type → Tangle type name
_TYPE_TO_TANGLE: dict[type, str] = {
    str: "String",
    int: "Integer",
    float: "Float",
    bool: "Boolean",
    list: "JsonArray",
    dict: "JsonObject",
}

# Tangle type name → argparse deserializer expression
_TYPE_TO_DESERIALIZER: dict[str, str] = {
    "String": "str",
    "Integer": "int",
    "Float": "float",
    "Boolean": "_deserialize_bool",
    "JsonArray": "json.loads",
    "JsonObject": "json.loads",
}

# Tangle type names that need extra definitions in the generated code
_TYPE_DEFINITIONS: dict[str, str] = {
    "Boolean": textwrap.dedent("""\
        def _deserialize_bool(s):
            s = s.lower()
            if s in ("true", "1", "yes"):
                return True
            if s in ("false", "0", "no"):
                return False
            raise TypeError(
                f'Error parsing "{s}" as bool value. Supported values: "true", "false", "1", "0".'
            )"""),
    "JsonArray": "import json",
    "JsonObject": "import json",
}

_MAKE_PARENT_DIRS_HELPER = textwrap.dedent("""\
    def _make_parent_dirs_and_return_path(file_path: str):
        import os
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        return file_path""")

# Tangle type name → output serializer expression (for NamedTuple return fields)
_TYPE_TO_SERIALIZER: dict[str, str] = {
    "String": "_serialize_str",
    "Integer": "str",
    "Float": "str",
    "Boolean": "str",
    "JsonArray": "json.dumps",
    "JsonObject": "json.dumps",
}

_SERIALIZE_STR_HELPER = textwrap.dedent("""\
    def _serialize_str(str_value) -> str:
        if isinstance(str_value, str):
            return str_value
        else:
            return str(str_value)""")


# ============================================================================
# Data structures
# ============================================================================


@dataclass
class ParamInfo:
    """Describes a single function parameter mapped to a component input or output."""

    name: str  # Python parameter name
    yaml_name: str  # Name in YAML (may have _path/_file suffix stripped)
    python_type: str | None  # Original Python type annotation string
    tangle_type: str | None  # Tangle type: String, Integer, Float, etc.
    kind: Literal["input", "output", "input_path", "return_output"]
    description: str | None = None
    default: Any = inspect.Parameter.empty
    optional: bool = False
    deserializer: str = "str"  # argparse type= expression


@dataclass
class FunctionSpec:
    """Complete specification of a function for component generation."""

    name: str
    component_name: str
    description: str | None
    params: list[ParamInfo] = field(default_factory=list)
    return_params: list[ParamInfo] = field(default_factory=list)  # Return value outputs
    single_return_output: bool = False  # True when -> str (not NamedTuple); needs _outputs=[_outputs] wrapping
    source_code: str = ""
    source_code_stripped: str = ""
    module_source_stripped: str = ""  # Full module source (for bundle mode)
    docstring_metadata: dict[str, str] = field(default_factory=dict)  # name, version, updated_at from Metadata:

    @property
    def inputs(self) -> list[ParamInfo]:
        return [p for p in self.params if p.kind in ("input", "input_path")]

    @property
    def outputs(self) -> list[ParamInfo]:
        """OutputPath parameter outputs."""
        return [p for p in self.params if p.kind == "output"]

    @property
    def all_outputs(self) -> list[ParamInfo]:
        """All outputs: OutputPath parameters + NamedTuple return fields."""
        return self.outputs + self.return_params


# ============================================================================
# Module loading
# ============================================================================


def _ensure_cloud_pipelines_shim() -> None:
    """Register the import-time ``cloud_pipelines`` shim used while introspecting
    authoring files.

    This lets us load Python files that use ``from cloud_pipelines import
    components`` without requiring that authoring package to be installed; the
    authoring constructs are stripped from the generated runtime code later.

    OSS deliberately does NOT fabricate a shim for any *downstream* authoring
    surface (e.g. a module a downstream package exposes to re-export the
    authoring objects under its own import path). A downstream package that
    wants its own authoring path recognised both makes that module importable
    itself and registers it via :func:`register_authoring_import_module`.
    """
    if "cloud_pipelines" not in sys.modules:
        components_mod = types.ModuleType("cloud_pipelines.components")
        setattr(components_mod, "InputPath", InputPath)
        setattr(components_mod, "OutputPath", OutputPath)

        cloud_pipelines_mod = types.ModuleType("cloud_pipelines")
        setattr(cloud_pipelines_mod, "components", components_mod)

        sys.modules["cloud_pipelines"] = cloud_pipelines_mod
        sys.modules["cloud_pipelines.components"] = components_mod


def load_python_module(file_path: Path, extra_sys_path: list[Path] | None = None) -> Any:
    """Dynamically import a Python module from a file path.

    Args:
        file_path: Path to the Python source file.
        extra_sys_path: Additional directories to add to ``sys.path`` during
            module loading.  This is needed when the module imports sibling
            packages that live outside ``file_path.parent`` (e.g. when
            ``--resolve-root`` points at a parent ``src/`` directory).
    """
    _ensure_cloud_pipelines_shim()

    module_name = file_path.stem
    spec = importlib.util.spec_from_file_location(module_name, location=str(file_path))
    if not spec or not spec.loader:
        raise ValueError(f"Unable to create module spec for {file_path}")
    module = importlib.util.module_from_spec(spec)
    # Add the module's directory to sys.path so relative imports work
    module_dir = str(file_path.parent.resolve())
    original_path = sys.path.copy()
    if module_dir not in sys.path:
        sys.path.insert(0, module_dir)
    # Add extra directories (e.g. resolve_root) so sibling imports resolve
    if extra_sys_path:
        for p in reversed(extra_sys_path):
            p_str = str(p.resolve())
            if p_str not in sys.path:
                sys.path.insert(0, p_str)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path = original_path
    return module


def get_function_from_module(module: Any, function_name: str | None = None) -> Callable:
    """Get a function from a loaded module.

    If function_name is specified, returns that function.
    Otherwise, returns the single public function (errors if 0 or >1).
    """
    if function_name:
        func = getattr(module, function_name, None)
        if func is None or not callable(func):
            raise ValueError(f"Function '{function_name}' not found in module {module.__name__}")
        return func

    functions = [
        getattr(module, name)
        for name in dir(module)
        if not name.startswith("_") and callable(getattr(module, name)) and not isinstance(getattr(module, name), type)
    ]

    if not functions:
        raise ValueError(f"No public functions found in module {module.__name__}")
    if len(functions) > 1:
        names = [f.__name__ for f in functions]
        raise ValueError(
            f"Found multiple functions in module {module.__name__}: {names}. " "Please specify --function-name."
        )
    return functions[0]


# ============================================================================
# Type annotation resolution
# ============================================================================


def _resolve_annotation(annotation: Any) -> tuple[str | None, str, Literal["input", "output", "input_path"]]:
    """Resolve a parameter annotation to (tangle_type, deserializer, kind).

    Returns:
        (tangle_type, deserializer_code, kind)
    """
    if annotation is inspect.Parameter.empty or annotation is None:
        return "String", "str", "input"

    # Handle InputPath / OutputPath (both our local versions and cloud_pipelines versions)
    type_name = type(annotation).__name__
    if type_name == "OutputPath":
        inner_type = getattr(annotation, "type", None) or "String"
        return inner_type, "_make_parent_dirs_and_return_path", "output"
    if type_name == "InputPath":
        inner_type = getattr(annotation, "type", None) or "String"
        return inner_type, "str", "input_path"

    # Handle generic types first: Optional[T], list[T], dict[K,V], Union[T, None]
    # Must come before isinstance(type) check because list[str] passes isinstance(type) in Python 3.10
    origin = typing.get_origin(annotation)
    if origin in (list,):
        return "JsonArray", "json.loads", "input"
    if origin in (dict,):
        return "JsonObject", "json.loads", "input"
    if origin is typing.Union or origin is types.UnionType:
        args = typing.get_args(annotation)
        # Optional[T] == Union[T, None]
        if len(args) == 2 and type(None) in args:
            non_none = args[0] if args[1] is type(None) else args[1]
            return _resolve_annotation(non_none)
        return None, "str", "input"

    # Handle direct Python types (after generic check)
    if isinstance(annotation, type):
        tangle = _TYPE_TO_TANGLE.get(annotation)
        if tangle:
            return tangle, _TYPE_TO_DESERIALIZER[tangle], "input"
        return str(annotation.__name__), "str", "input"

    # ForwardRef or other annotation — use string representation
    return str(getattr(annotation, "__forward_arg__", annotation)), "str", "input"


def _make_return_param(name: str, annotation: type) -> ParamInfo:
    """Create a ParamInfo for a return value output."""
    tangle_type = _TYPE_TO_TANGLE.get(annotation, "String")
    return ParamInfo(
        name=name,
        yaml_name=name,
        python_type=str(annotation) if annotation else None,
        tangle_type=tangle_type,
        kind="return_output",
        description=None,
        deserializer=_TYPE_TO_SERIALIZER.get(tangle_type, "_serialize_str"),
    )


def _resolve_namedtuple_return(return_ann: Any) -> list[ParamInfo]:
    """Extract output parameters from a NamedTuple return annotation."""
    # __annotations__ doesn't exist in python 3.5 and earlier
    # _field_types doesn't exist in python 3.9 and later
    field_annotations = getattr(return_ann, "__annotations__", None) or getattr(return_ann, "_field_types", None)
    return [
        _make_return_param(
            name=field_name,
            annotation=field_annotations.get(field_name, str) if field_annotations else str,
        )
        for field_name in return_ann._fields
    ]


def _resolve_single_return(return_ann: type) -> ParamInfo | None:
    """Create an output parameter for a single (non-NamedTuple) return type.

    Returns None if the type is not a recognized Tangle type.
    """
    if return_ann not in _TYPE_TO_TANGLE:
        return None
    return _make_return_param(name="Output", annotation=return_ann)


def _resolve_return_type(func: Callable) -> tuple[list[ParamInfo], bool]:
    """Extract output parameters from the function's return type annotation.

    Matches the Cloud-Pipelines SDK behavior:
    - NamedTuple return -> one output per field (multi-output)
    - Single type return (str, int, etc.) -> one output named "Output" (single-output)
    - No return annotation -> no outputs

    Returns:
        (return_params, single_return_output) where single_return_output is True
        when the return is a plain type (not NamedTuple) and the generated code
        needs ``_outputs = [_outputs]`` wrapping.
    """
    # Use inspect.signature like the SDK does (avoids typing.get_type_hints issues
    # with InputPath/OutputPath instances that aren't valid types for Optional[]).
    return_ann = inspect.signature(func).return_annotation
    if return_ann is None or return_ann is inspect.Parameter.empty:
        return [], False

    if hasattr(return_ann, "_fields"):
        return _resolve_namedtuple_return(return_ann), False

    param = _resolve_single_return(return_ann)
    if param:
        return [param], True

    return [], False


# ============================================================================
# Interface extraction
# ============================================================================


def _python_name_to_component_name(name: str) -> str:
    """Convert a Python function name to a human-readable component name."""
    name_with_spaces = re.sub(" +", " ", name.replace("_", " ")).strip()
    if not name_with_spaces:
        return name
    return name_with_spaces[0].upper() + name_with_spaces[1:]


def extract_docstring_metadata(docstring: str) -> dict[str, str]:
    """Extract metadata and description from a docstring.

    Extracts the main description text (before any sections) and key-value pairs
    from the Metadata section:

        Processes and validates input data.

        Metadata:
            name: My Component Name
            version: 1.2
            updated_at: 2025-01-01T00:00:00Z

        Args:
            ...

    Returns:
        Dict with keys like "description", "name", "version", "updated_at" (only present if found).
    """
    sections = [
        "args",
        "arguments",
        "parameters",
        "returns",
        "raises",
        "yields",
        "note",
        "notes",
        "example",
        "examples",
        "metadata",
    ]

    metadata: dict[str, str] = {}
    in_metadata = False
    in_description = True
    description_lines: list[str] = []

    for line in docstring.split("\n"):
        stripped = line.strip()

        # Check for section headers
        if stripped and stripped.rstrip(":").lower() in sections:
            in_description = False
            if stripped.lower() == "metadata:":
                in_metadata = True
            elif in_metadata:
                break
            continue

        if in_metadata:
            # Parse any key: value pair
            kv_match = re.match(r"^(\w[\w_]*)\s*:\s*(.+)", stripped)
            if kv_match:
                key = kv_match.group(1).lower()
                value = kv_match.group(2).strip()
                # Normalize version_timestamp to updated_at
                if key == "version_timestamp":
                    key = "updated_at"
                metadata[key] = value
        elif in_description:
            # Collect description lines (before any section)
            if stripped:
                description_lines.append(stripped)

    if description_lines:
        metadata["description"] = " ".join(description_lines)

    return metadata


def find_function_in_source(
    file_path: Path, function_name: str | None = None
) -> tuple[str | None, ast.FunctionDef | None]:
    """Find a function in a Python source file by AST parsing.

    Args:
        file_path: Path to the Python file
        function_name: Name of function to find. If not found or not provided,
                       falls back to first public function in the file.

    Returns:
        Tuple of (function_name, function_node) or (None, None) if no functions found.
    """
    try:
        content = file_path.read_text()
        tree = ast.parse(content)

        all_functions = [
            node
            for node in ast.iter_child_nodes(tree)
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
        ]

        if not all_functions:
            return None, None

        if function_name:
            for func in all_functions:
                if func.name == function_name:
                    return func.name, func
            # Function not found, fall back to first function
            first_func = all_functions[0]
            warnings.warn(
                f"Function '{function_name}' not found in {file_path.name}, " f"using '{first_func.name}' instead"
            )
            return first_func.name, first_func

        first_func = all_functions[0]
        return first_func.name, first_func

    except (SyntaxError, ValueError, OSError) as e:
        warnings.warn(f"Could not parse {file_path}: {e}")
        return None, None


def extract_file_metadata(file_path: Path, function_name: str | None = None) -> tuple[dict[str, str], str | None]:
    """Extract metadata from a function's docstring in a Python source file.

    Finds the function via AST, extracts its docstring, and parses the Metadata
    section for keys like name, version, updated_at, plus the description.

    Args:
        file_path: Path to the Python file
        function_name: Function to extract from. Defaults to file stem.

    Returns:
        Tuple of (metadata_dict, actual_function_name_used)
    """
    if not function_name:
        function_name = file_path.stem.replace("-", "_")

    actual_func_name, func_node = find_function_in_source(file_path, function_name)
    if not func_node:
        return {}, None

    docstring = ast.get_docstring(func_node)
    if docstring:
        return extract_docstring_metadata(docstring), actual_func_name

    return {}, actual_func_name


def extract_interface(
    func: Callable,
    docstring_metadata: dict[str, str],
) -> FunctionSpec:
    """Extract component interface from a Python function.

    Uses inspect.signature() for parameter info and docstring_parser for descriptions.

    Args:
        func: The Python function to introspect.
        docstring_metadata: Metadata from extract_file_metadata or extract_docstring_metadata.
    """
    signature = inspect.signature(func)
    parsed_docstring = docstring_parser.parse(inspect.getdoc(func) or "")
    doc_dict = {p.arg_name: p.description for p in parsed_docstring.params}

    params: list[ParamInfo] = []

    for param in signature.parameters.values():
        annotation = param.annotation
        tangle_type, deserializer, kind = _resolve_annotation(annotation)

        # Determine the YAML name (strip _path/_file suffixes for InputPath/OutputPath)
        yaml_name = param.name
        if kind in ("output", "input_path"):
            if yaml_name.endswith("_path"):
                yaml_name = yaml_name[: -len("_path")]
            elif yaml_name.endswith("_file"):
                yaml_name = yaml_name[: -len("_file")]

        # Determine optionality and default
        optional = False
        default = inspect.Parameter.empty
        if param.default is not inspect.Parameter.empty:
            if kind == "input":
                optional = True
                default = param.default
            elif kind == "input_path" and param.default is None:
                optional = True

        params.append(
            ParamInfo(
                name=param.name,
                yaml_name=yaml_name,
                python_type=str(annotation) if annotation is not inspect.Parameter.empty else None,
                tangle_type=tangle_type,
                kind=kind,
                description=doc_dict.get(param.name),
                default=default,
                optional=optional,
                deserializer=deserializer,
            )
        )

    component_name = docstring_metadata.get("name") or _python_name_to_component_name(func.__name__)
    description = parsed_docstring.description
    if description:
        # Strip Metadata: section that docstring_parser doesn't understand
        desc_lines = []
        for line in description.split("\n"):
            if line.strip().lower() == "metadata:":
                break
            desc_lines.append(line)
        description = "\n".join(desc_lines).strip()

    # Get source code
    source_code = ""
    source_code_stripped = ""
    module_source_stripped = ""
    try:
        raw_source = inspect.getsource(func)
        source_code = textwrap.dedent(raw_source)
        # Remove decorators
        lines = source_code.split("\n")
        while lines and not lines[0].startswith("def "):
            del lines[0]
        source_code = "\n".join(lines)
        source_code_stripped = _strip_type_hints(source_code)

        # module_source_stripped is populated externally via generate_component_yaml
        # (since we have the file path there but not here)
    except (OSError, TypeError) as e:
        warnings.warn(f"Could not get source code for {func.__name__}: {e}")

    # Extract return type outputs (NamedTuple or single value)
    return_params, single_return_output = _resolve_return_type(func)

    # Enrich return_params with descriptions from docstring Returns section.
    # docstring_parser interprets "field_name: description" under Returns as
    # type_name=field_name, so we check both return_name and type_name.
    if return_params and parsed_docstring.many_returns:
        returns_dict: dict[str, str] = {}
        for r in parsed_docstring.many_returns:
            name = r.return_name or r.type_name
            if name and r.description:
                returns_dict[name] = r.description
        for rp in return_params:
            if rp.name in returns_dict:
                rp.description = returns_dict[rp.name]

    return FunctionSpec(
        name=func.__name__,
        component_name=component_name,
        description=description,
        params=params,
        return_params=return_params,
        single_return_output=single_return_output,
        source_code=source_code,
        source_code_stripped=source_code_stripped,
        module_source_stripped=module_source_stripped,
        docstring_metadata=docstring_metadata,
    )


# ============================================================================
# __main__ guard stripping
# ============================================================================


def _strip_main_guard(source_code: str) -> str:
    """Remove ``if __name__ == "__main__":`` blocks from source code.

    These guards conflict with the generated argparse wrapper because both
    execute at module level.  When the guard appears *before* the wrapper it
    fires first and typically calls ``sys.exit()``, preventing the component
    from running.
    """
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return source_code

    lines = source_code.splitlines(keepends=True)

    # Collect line ranges to remove (1-indexed, inclusive)
    ranges_to_remove: list[tuple[int, int]] = []
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.If):
            continue
        if _is_name_main_test(node.test):
            start = node.lineno
            end = node.end_lineno or node.lineno
            ranges_to_remove.append((start, end))

    if not ranges_to_remove:
        return source_code

    removed: set[int] = set()
    for start, end in ranges_to_remove:
        removed.update(range(start, end + 1))

    kept = [line for i, line in enumerate(lines, 1) if i not in removed]
    return "".join(kept)


def _is_name_main_test(node: ast.expr) -> bool:
    """Return True if *node* is ``__name__ == "__main__"`` (in either order)."""
    if not isinstance(node, ast.Compare):
        return False
    if len(node.ops) != 1 or not isinstance(node.ops[0], ast.Eq):
        return False
    if len(node.comparators) != 1:
        return False

    left = node.left
    right = node.comparators[0]

    def _is_dunder_name(n: ast.expr) -> bool:
        return isinstance(n, ast.Name) and n.id == "__name__"

    def _is_main_str(n: ast.expr) -> bool:
        return isinstance(n, ast.Constant) and n.value == "__main__"

    return (_is_dunder_name(left) and _is_main_str(right)) or (_is_main_str(left) and _is_dunder_name(right))


# ============================================================================
# Authoring-construct stripping (authoring imports + @task/@pipeline/@subpipeline/@registered)
# ============================================================================

# Decorators that exist purely to *record* a function at authoring time. They
# must never survive into the baked operation program (see
# _strip_authoring_constructs). ``registered`` marks an op published separately
# via its own gen_config.yaml; when that same op is baked (through its
# local_from_python entry) the decorator + its authoring import must be stripped
# too, exactly like @task.
_AUTHORING_DECORATOR_NAMES = frozenset({"task", "pipeline", "subpipeline", "registered"})

# The python-pipeline authoring modules. ONLY imports of these modules (and
# their submodules) are authoring-only and stripped from the baked source. We
# deliberately do NOT strip other packages that merely share a top-level name
# (e.g. a downstream ``*.utils``): those may be legitimate runtime helpers used
# inside a ``@task`` body, and dropping them would raise ``NameError`` in the
# operation container.
#
# OSS recognises exactly one authoring surface out of the box: the canonical
# ``tangle_cli.python_pipeline`` path. A downstream package that re-exports the
# authoring objects under its own module path — so authors may write ``from
# <downstream>.python_pipeline import task`` — registers that path via
# :func:`register_authoring_import_module`; codegen then strips either import
# the same way. OSS never hardcodes a downstream module name (the dependency
# points inward), mirroring the resolver/reader registries in the hydrator.
_AUTHORING_IMPORT_MODULES: list[str] = ["tangle_cli.python_pipeline"]


def register_authoring_import_module(module: str) -> None:
    """Register *module* as an additional python-pipeline authoring surface.

    A downstream package that re-exports the ``tangle_cli.python_pipeline``
    authoring objects under its own module path calls this (typically at import
    time) so codegen strips ``from <module> import ...`` / ``import <module>``
    lines — and their submodules — from baked runtime source exactly like the
    canonical OSS surface. Idempotent: registering an already-known module is a
    no-op, so repeated import-time registration is safe.
    """
    if module not in _AUTHORING_IMPORT_MODULES:
        _AUTHORING_IMPORT_MODULES.append(module)


def authoring_import_modules() -> tuple[str, ...]:
    """Return the python-pipeline authoring modules recognised by codegen."""
    return tuple(_AUTHORING_IMPORT_MODULES)

# The authoring-only ``TaskEnv`` class name. A module-level ``X = TaskEnv(...)``
# (or ``X = <alias>.TaskEnv(...)``) declaration is authoring-only by contract and
# is stripped from the baked source by ``_strip_authoring_constructs``.
# Matched by trailing NAME only (like the authoring decorators), because in
# python-pipeline authoring files ``TaskEnv`` always resolves to the
# python-pipeline authoring surface's ``TaskEnv``.
_AUTHORING_ENV_CLASS_NAME = "TaskEnv"


class AuthoringStripError(ValueError):
    """Raised when env-only authoring code cannot be safely stripped.

    The TaskEnv runtime-strip hardening (``_strip_authoring_constructs``)
    raises this when a ``@task(env=...)`` env binding is entangled with
    runtime code — e.g. a mixed ``from _envs import UPI, helper`` import whose
    ``helper`` is used at runtime, or a collected env name referenced by the
    kept task body. Failing fast here is intentional: silently baking a broken
    ``from _envs import UPI`` / ``UPI = TaskEnv(...)`` would only surface as a
    ``NameError`` / ``ImportError`` at container start. The message tells the
    author how to split the import or keep TaskEnv values authoring-only.
    """


def _decorator_called_name(node: ast.expr) -> str | None:
    """Return the trailing name a decorator expression resolves to.

    Handles ``@name`` / ``@name(...)`` and ``@mod.name`` / ``@mod.name(...)``
    forms, returning the trailing attribute/name (e.g. ``task`` for both
    ``@task(...)`` and ``@tangle_cli.python_pipeline.task(...)``). Returns
    ``None`` for shapes we do not recognise so callers leave them untouched.

    Limitation (v1, intentional): matching is by trailing NAME only, not by
    import resolution. A hypothetical unrelated ``@some_other_lib.task(...)``
    decorator would therefore also match. This is acceptable because in
    python-pipeline authoring files the only decorators named ``task`` /
    ``pipeline`` / ``subpipeline`` are the authoring decorators; resolving the
    import binding is deferred unless a real collision appears.
    """
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_authoring_module(name: str) -> bool:
    """Return True if *name* is an authoring module or a submodule of one."""
    return any(name == mod or name.startswith(mod + ".") for mod in _AUTHORING_IMPORT_MODULES)


def _is_authoring_import(node: ast.stmt) -> bool:
    """Return True if *node* imports the python-pipeline authoring surface.

    Matches ONLY the registered authoring modules (and their submodules) — the
    canonical ``tangle_cli.python_pipeline`` plus any registered via
    :func:`register_authoring_import_module`:

    - ``from tangle_cli.python_pipeline import ...`` (including the aliased
      ``from tangle_cli.python_pipeline import ref as operation_by_ref`` form
      and submodules like ``from tangle_cli.python_pipeline.x import y``);
    - ``import tangle_cli.python_pipeline`` / ``import
      tangle_cli.python_pipeline as tp``;
    - the equivalents for any registered downstream authoring path.

    It does NOT match other packages that merely share a top-level name (e.g. a
    downstream ``*.utils`` module) — those can be genuine runtime helpers
    referenced inside a ``@task`` body and must survive into the baked program.
    Relative imports (``from . import x``) are never authoring imports.
    """
    if isinstance(node, ast.ImportFrom):
        if node.level:  # relative import — not the authoring package
            return False
        return _is_authoring_module(node.module or "")
    if isinstance(node, ast.Import):
        return any(_is_authoring_module(alias.name) for alias in node.names)
    return False


def _attr_root_name(node: ast.expr) -> str | None:
    """Return the root ``Name`` id of an attribute chain (``a.b.c`` -> ``a``).

    Returns ``None`` for shapes that don't bottom out in a plain ``Name``
    (e.g. ``foo().bar``), so callers leave them untouched.
    """
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _env_keyword_binding_name(call: ast.Call) -> str | None:
    """Return the module-level authoring name a ``@task(env=...)`` keyword needs.

    Inspects the ``env=`` keyword of a (stripped) ``@task(...)`` decorator and
    returns the name of the module-level binding that must also be stripped so
    the baked runtime program does not crash referencing an authoring-only name:

    - ``env=UPI`` -> ``"UPI"`` (a module-level env *binding* to strip, either an
      ``UPI = TaskEnv(...)`` assignment or a ``from _envs import UPI`` import);
    - ``env=_envs.UPI`` -> ``"_envs"`` (the module-alias root, so the
      ``import _envs`` line can be stripped);
    - ``env=TaskEnv(...)`` / ``env=tp.TaskEnv(...)`` (inline) -> ``None``: the
      whole decorator line range is already deleted, so there is no residual
      module-level binding to strip;
    - anything else -> ``None`` (leave it untouched).
    """
    for keyword in call.keywords:
        if keyword.arg != "env":
            continue
        value = keyword.value
        if isinstance(value, ast.Name):
            return value.id
        if isinstance(value, ast.Attribute):
            return _attr_root_name(value)
        # env=TaskEnv(...) / env=tp.TaskEnv(...) inline, or any other shape:
        # the decorator range already covers it, no residual binding.
        return None
    return None


def _is_task_env_construction(value: ast.expr | None) -> bool:
    """True if *value* is a direct ``TaskEnv(...)`` / ``<alias>.TaskEnv(...)`` call.

    Matched by trailing call name (mirroring ``_decorator_called_name``), so
    both ``TaskEnv(image=...)`` and ``tp.TaskEnv(image=...)`` qualify. Used to
    detect module-level env declarations like ``UPI = TaskEnv(...)`` regardless
    of whether a ``@task(env=UPI)`` references them.
    """
    return isinstance(value, ast.Call) and _decorator_called_name(value) == _AUTHORING_ENV_CLASS_NAME


def _import_bound_names(node: ast.Import | ast.ImportFrom) -> dict[str, ast.alias]:
    """Map each name a top-level import binds into the namespace to its alias.

    - ``from m import UPI`` -> ``{"UPI": alias}``
    - ``from m import UPI as U`` -> ``{"U": alias}``
    - ``import _envs`` -> ``{"_envs": alias}`` (root of a dotted module path)
    - ``import a.b.c`` -> ``{"a": alias}``
    - ``import envs as task_envs`` -> ``{"task_envs": alias}``
    """
    bound: dict[str, ast.alias] = {}
    for alias in node.names:
        if alias.asname:
            bound[alias.asname] = alias
        elif isinstance(node, ast.Import):
            # ``import a.b.c`` binds only the top-level package ``a``.
            bound[alias.name.split(".", 1)[0]] = alias
        else:
            bound[alias.name] = alias
    return bound


def _annotation_name_node_ids(tree: ast.AST) -> set[int]:
    """Return ``id()`` of every ``ast.Name`` that lives inside a type-annotation slot.

    Annotation slots are stripped from the baked output by ``_strip_type_hints``
    (which runs AFTER ``_strip_authoring_constructs``), so a name that appears
    ONLY in an annotation is NOT a live runtime reference. Excluding these from
    the fail-fast reference scan prevents a false positive where an env name
    used only as a parameter/return type annotation (``def f(x: UPI) -> UPI:``)
    is mistaken for a kept runtime reference (FIX N1, §3.5).

    Annotation slots covered (matching ``_strip_type_hints_ast``):

    - function parameter annotations: ``args.args`` / ``posonlyargs`` /
      ``kwonlyargs`` plus ``*args`` (``vararg``) and ``**kwargs`` (``kwarg``);
    - ``FunctionDef`` / ``AsyncFunctionDef`` return annotations (``-> T``);
    - ``AnnAssign`` annotations (``x: T`` / ``x: T = ...``).

    Because ``tree`` stays alive for the duration of the caller, every node's
    ``id()`` is stable and unique, so identity membership is reliable.
    """
    annotation_slots: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            for arg in (
                *args.posonlyargs,
                *args.args,
                *args.kwonlyargs,
                args.vararg,
                args.kwarg,
            ):
                if arg is not None and arg.annotation is not None:
                    annotation_slots.append(arg.annotation)
            if node.returns is not None:
                annotation_slots.append(node.returns)
        elif isinstance(node, ast.AnnAssign):
            annotation_slots.append(node.annotation)

    name_ids: set[int] = set()
    for slot in annotation_slots:
        for sub in ast.walk(slot):
            if isinstance(sub, ast.Name):
                name_ids.add(id(sub))
    return name_ids


def _strip_authoring_constructs(source_code: str) -> str:
    """Strip python-pipeline authoring imports and decorators from baked source.

    The generated operation container re-executes ``module_source_stripped`` at
    startup and then calls the target function directly. Authoring constructs
    must NOT survive into that runtime program:

    - re-running an ``@task`` / ``@pipeline`` / ``@subpipeline`` decorator
      replaces the function with a ``CallableRef`` recorder, which raises at
      call time because there is no active ``@pipeline`` trace context;
    - on a thin image the ``from tangle_cli.python_pipeline import ...``
      import itself can fail with ``ImportError``.

    This removes them via surgical AST line-range deletion (mirroring
    ``_strip_main_guard``), so comments/formatting in the rest of the source
    survive — we deliberately avoid a full ``ast.unparse`` round-trip.

    Contract this relies on: authoring-surface names (``task``, ``pipeline``,
    ``subpipeline``, ``In``, ``Out``, ``Outputs``, ``ref``, ...) appear ONLY in
    decorators and type annotations — both stripped before the source is baked —
    never in a runtime function body. Dropping the whole authoring import line is
    therefore safe.

    Scope of the strip (intentional v1 boundaries):

    - imports: only the registered authoring modules (and submodules) are
      dropped — see ``_is_authoring_import``. Other runtime helpers that merely
      share a top-level name are preserved.
    - decorators: matched by trailing NAME (``task`` / ``pipeline`` /
      ``subpipeline``), not by import resolution — see ``_decorator_called_name``
      for the limitation. Unrelated decorators (``@functools.cache``,
      ``@property``, ...) are preserved.

    TaskEnv authoring-strip hardening (``@task(env=...)``): an env
    declaration that exists ONLY to feed a stripped ``@task(env=...)`` decorator
    would otherwise crash the baked program (``NameError: TaskEnv`` for a
    co-located ``UPI = TaskEnv(...)`` whose import was stripped, or
    ``ImportError`` for a ``from _envs import UPI`` whose module is not in the
    runtime image). On top of the import/decorator strip this also removes, by
    line range:

    - every module-level ``X = TaskEnv(...)`` / ``X: TaskEnv = TaskEnv(...)``
      declaration (direct ``TaskEnv(...)`` construction), and
    - module-level bindings (assignment OR import) of any name a stripped
      ``@task(env=...)`` referenced — ``env=UPI`` collects ``UPI``
      (``UPI = TaskEnv(...)`` / ``UPI = make_task_env(...)`` / ``from _envs import
      UPI``); ``env=_envs.UPI`` collects the module alias ``_envs``
      (``import _envs``).

    It is deliberately narrow: only names PROVEN to participate in a stripped
    ``@task(env=...)`` decorator or a direct module-level ``TaskEnv(...)`` call
    are removed. It is NOT a general unused-import cleaner. It raises
    :class:`AuthoringStripError` (fail-fast) rather than bake a broken program
    when an env binding is entangled with runtime code: a mixed
    ``from _envs import UPI, helper`` whose ``helper`` is used at runtime, or a
    collected env name still referenced by the kept task body.

    This intentionally operates on ``module_source_stripped`` ONLY. It must never
    touch the verbatim ``python_original_code`` annotation, which is read
    directly from the source file elsewhere and kept byte-verbatim.
    """
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return source_code

    lines = source_code.splitlines(keepends=True)
    removed: set[int] = set()  # 1-indexed line numbers to drop
    # Names introduced ONLY to feed a stripped ``@task(env=...)`` decorator.
    # Collected from ``env=`` keywords; used below to strip the matching
    # module-level assignment/import binding.
    collected_env_names: set[str] = set()

    for node in ast.walk(tree):
        # Authoring imports — delete the whole (possibly multi-line) statement.
        if isinstance(node, (ast.Import, ast.ImportFrom)) and _is_authoring_import(node):
            start = node.lineno
            end = node.end_lineno or node.lineno
            removed.update(range(start, end + 1))
            continue

        # @task / @pipeline / @subpipeline decorators on functions/classes.
        # The "@" shares the decorator expression's first line, so removing the
        # node's full line range removes the "@" too. Real-world decorators span
        # multiple lines, hence lineno..end_lineno rather than a prefix match.
        decorator_list = getattr(node, "decorator_list", None)
        if not decorator_list:
            continue
        for decorator in decorator_list:
            if _decorator_called_name(decorator) in _AUTHORING_DECORATOR_NAMES:
                start = decorator.lineno
                end = decorator.end_lineno or decorator.lineno
                removed.update(range(start, end + 1))
                # Record the env-only authoring name this @task(env=...) needs
                # stripped from module scope (None for inline TaskEnv(...)).
                if isinstance(decorator, ast.Call):
                    env_name = _env_keyword_binding_name(decorator)
                    if env_name is not None:
                        collected_env_names.add(env_name)

    # --- Fail-fast: nested/conditional env imports cannot be stripped (N1/N2) -
    #
    # Module-level removal below only touches ``tree.body``. An env import
    # nested inside an ``if`` / ``try`` / function body (i.e. NOT a direct child
    # of ``tree.body``) is therefore NOT stripped and would LEAK into the baked
    # program -> ``ImportError`` on a thin runtime image (or re-binding an
    # authoring-only name) at container start. We also must NOT line-delete a
    # nested import: removing the only statement in a block leaves an empty
    # suite -> ``IndentationError``. Converting the silent leak into a loud,
    # actionable error is the correct, safe behavior (FIX N2, §3.5).
    if collected_env_names:
        top_level_stmt_ids = {id(stmt) for stmt in tree.body}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            if id(node) in top_level_stmt_ids:
                continue  # module-level imports are handled by the strip below
            nested_env = sorted(collected_env_names & _import_bound_names(node).keys())
            if nested_env:
                names_repr = ", ".join(repr(n) for n in nested_env)
                raise AuthoringStripError(
                    f"env name {names_repr} is imported inside a nested block "
                    "(if/try/function); TaskEnv env imports must be module-level "
                    "/ authoring-only. A nested env import is not stripped and "
                    "would leak into the baked runtime program (ImportError at "
                    "container start). Move it to a top-level import so it can be "
                    "stripped, and keep TaskEnv values authoring-only."
                )

    # --- TaskEnv env-only declarations / imports (§3.5) ---------------------
    #
    # Restricted to module-level statements (``tree.body``) so nested code is
    # never touched. Two kinds of statement are stripped:
    #   1. assignments that construct a TaskEnv directly (``X = TaskEnv(...)``)
    #      or whose target is a collected env name (``UPI = make_task_env(...)``
    #      when ``@task(env=UPI)`` was seen), and
    #   2. imports that bind a collected env name/module (``from _envs import
    #      UPI`` / ``import _envs``) when that name is env-only.
    #
    # We record each candidate's bound name(s) + line range, then verify (after
    # a reference scan) that removing it cannot break kept runtime code.
    env_assign_bindings: list[tuple[set[str], int, int]] = []  # (names, start, end)
    env_import_candidates: list[tuple[ast.Import | ast.ImportFrom, int, int]] = []
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            simple_targets = {t.id for t in stmt.targets if isinstance(t, ast.Name)}
            if _is_task_env_construction(stmt.value) or (simple_targets & collected_env_names):
                env_assign_bindings.append((simple_targets, stmt.lineno, stmt.end_lineno or stmt.lineno))
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            tname = stmt.target.id
            if _is_task_env_construction(stmt.value) or tname in collected_env_names:
                env_assign_bindings.append(({tname}, stmt.lineno, stmt.end_lineno or stmt.lineno))
        elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
            if _is_authoring_import(stmt):
                continue  # already removed above
            bound = _import_bound_names(stmt)
            if collected_env_names & bound.keys():
                env_import_candidates.append((stmt, stmt.lineno, stmt.end_lineno or stmt.lineno))

    # Provisionally drop every env declaration/import candidate. Their own line
    # ranges hold no runtime ``Load`` of the bound name (assignment targets are
    # ``Store``; import bindings are aliases), so including them now does not
    # mask a real runtime reference detected below.
    for _names, start, end in env_assign_bindings:
        removed.update(range(start, end + 1))
    for _stmt, start, end in env_import_candidates:
        removed.update(range(start, end + 1))

    # Reference scan: every ``Name`` used in a ``Load`` context, mapped to the
    # 1-indexed lines it appears on. Attribute roots (``_envs`` in
    # ``_envs.UPI``) are plain ``Name`` Load nodes too, so this covers them.
    #
    # FIX N1 (§3.5): exclude ``Name`` nodes that live in a type-annotation slot
    # (param/return/AnnAssign). Annotations are stripped from the baked output by
    # ``_strip_type_hints`` (which runs later), so an env name used ONLY as a
    # type annotation (``def f(x: UPI) -> UPI:``) is NOT a live runtime
    # reference and must not trip the body-ref fail-fast. A real body reference
    # (outside annotations) still records a Load and still fails fast.
    if env_assign_bindings or env_import_candidates:
        annotation_name_ids = _annotation_name_node_ids(tree)
        load_lines: dict[str, set[int]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and id(node) not in annotation_name_ids:
                load_lines.setdefault(node.id, set()).add(node.lineno)

        def _referenced_in_kept(name: str) -> bool:
            # ``name`` is used by runtime code iff it has a ``Load`` on a line
            # that survives the strip (i.e. not in ``removed``).
            return any(line not in removed for line in load_lines.get(name, ()))

        # Fail fast: a stripped env declaration whose target the kept body still
        # references would leave a dangling ``NameError`` — env names are
        # authoring-only by contract.
        for names, _start, _end in env_assign_bindings:
            for name in names:
                if _referenced_in_kept(name):
                    raise AuthoringStripError(
                        f"TaskEnv authoring name {name!r} is referenced by the "
                        "baked runtime code, but its declaration is stripped "
                        "because it is a @task(env=...) environment. TaskEnv "
                        "values are authoring-only: do not reference them from "
                        "a task body or other runtime code. Move the runtime "
                        "use out, or keep the value as a plain runtime object "
                        "that is not used as @task(env=...)."
                    )

        for stmt, _start, _end in env_import_candidates:
            bound = _import_bound_names(stmt)
            env_bound = collected_env_names & bound.keys()
            other_bound = bound.keys() - env_bound
            # (a) Mixed import: an env-only name shares the statement with a
            # runtime name that is actually used. We cannot line-delete just
            # part of the statement, so fail fast with split guidance.
            used_others = sorted(n for n in other_bound if _referenced_in_kept(n))
            if used_others:
                raise AuthoringStripError(
                    "Import " + ", ".join(sorted(env_bound)) + " is a @task(env=...) environment but shares an import "
                    "statement with runtime name(s) "
                    + ", ".join(used_others)
                    + ". Split the import so TaskEnv env names are imported on "
                    "their own line (e.g. `from _envs import UPI` separate from "
                    "`from _envs import helper`); env imports are authoring-only "
                    "and stripped from the baked runtime program."
                )
            # (b) The env name itself is still referenced by kept runtime code.
            for name in sorted(env_bound):
                if _referenced_in_kept(name):
                    raise AuthoringStripError(
                        f"TaskEnv authoring name {name!r} is imported and "
                        "referenced by the baked runtime code, but its import is "
                        "stripped because it is a @task(env=...) environment. "
                        "TaskEnv values are authoring-only: do not reference "
                        "them from a task body or other runtime code."
                    )

    if not removed:
        return source_code

    kept = [line for i, line in enumerate(lines, 1) if i not in removed]
    return "".join(kept)


# ============================================================================
# Type hint stripping (replicating SDK strip_type_hints)
# ============================================================================


def _strip_type_hints(source_code: str) -> str:
    """Strip type annotations from function definitions using the ast module."""
    try:
        return _strip_type_hints_ast(source_code)
    except Exception as e:
        warnings.warn(f"Failed to strip type hints (using source as-is): {e}")
        return source_code


def _byte_col_to_char_col(line: str, byte_col: int) -> int:
    """Convert a UTF-8 byte offset to a Python string character index.

    AST col_offset/end_col_offset are UTF-8 byte offsets, not character indices.
    For ASCII-only lines they're identical, but non-ASCII characters (e.g. "café")
    cause the two to diverge.
    """
    return len(line.encode("utf-8")[:byte_col].decode("utf-8", errors="replace"))


def _strip_type_hints_ast(source_code: str) -> str:
    """Strip type annotations from function definitions using the ast module.

    Removes parameter annotations (`: type`) and return annotations (`-> type`)
    from all function definitions. Uses AST to locate annotations, then performs
    surgical string removal to preserve original formatting.
    """
    tree = ast.parse(source_code)
    lines = source_code.splitlines(keepends=True)

    # Collect (line, col_start, col_end) ranges to remove, in source order.
    # We'll process them in reverse order so removals don't shift earlier offsets.
    # All columns here are character indices (converted from AST byte offsets).
    removals: list[tuple[int, int, int, int]] = []  # (start_line, start_col, end_line, end_col)

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        # --- Return annotation: remove " -> <type>" before the colon ---
        if node.returns is not None:
            ret = node.returns
            ret_start_line = ret.lineno  # 1-indexed
            ret_line_text = lines[ret_start_line - 1]
            ret_start_col = _byte_col_to_char_col(ret_line_text, ret.col_offset)
            ret_end_line = ret.end_lineno or ret_start_line
            ret_end_line_text = lines[ret_end_line - 1]
            ret_end_col = _byte_col_to_char_col(ret_end_line_text, ret.end_col_offset or (ret.col_offset + 1))

            # Find the "->" token by scanning backwards from the annotation start.
            # The arrow may be on the same line as the type, or on a preceding line
            # (e.g. `def f()\n  -> str:`), so we search backwards through lines.
            # Bound the search to the def line to avoid matching a previous function.
            min_line_idx = node.lineno - 1  # 0-indexed; the "def" line
            arrow_line_idx = ret_start_line - 1  # 0-indexed
            arrow_pos = -1
            while arrow_line_idx >= min_line_idx:
                search_region = lines[arrow_line_idx]
                if arrow_line_idx == ret_start_line - 1:
                    search_region = search_region[:ret_start_col]
                arrow_pos = search_region.rfind("->")
                if arrow_pos != -1:
                    break
                arrow_line_idx -= 1

            if arrow_pos != -1:
                # Strip any whitespace before the arrow too
                strip_start = arrow_pos
                line_text = lines[arrow_line_idx]
                while strip_start > 0 and line_text[strip_start - 1] == " ":
                    strip_start -= 1
                removals.append((arrow_line_idx + 1, strip_start, ret_end_line, ret_end_col))

        # --- Parameter annotations: remove ": <type>" from each arg ---
        for arg in node.args.args + node.args.posonlyargs + node.args.kwonlyargs:
            if arg.annotation is None:
                continue
            ann = arg.annotation
            # The annotation text starts after "param_name" with ": "
            # arg node: name at (arg.lineno, arg.col_offset), length = len(arg.arg)
            arg_line_text = lines[arg.lineno - 1]
            name_end_col = _byte_col_to_char_col(arg_line_text, arg.col_offset) + len(arg.arg)
            ann_end_line = ann.end_lineno or ann.lineno
            ann_end_line_text = lines[ann_end_line - 1]
            ann_end_col = _byte_col_to_char_col(ann_end_line_text, ann.end_col_offset or (ann.col_offset + 1))
            removals.append((arg.lineno, name_end_col, ann_end_line, ann_end_col))

        # vararg (*args) and kwarg (**kwargs)
        for maybe_arg in (node.args.vararg, node.args.kwarg):
            if maybe_arg is not None and maybe_arg.annotation is not None:
                ann = maybe_arg.annotation
                arg_line_text = lines[maybe_arg.lineno - 1]
                name_end_col = _byte_col_to_char_col(arg_line_text, maybe_arg.col_offset) + len(maybe_arg.arg)
                ann_end_line = ann.end_lineno or ann.lineno
                ann_end_line_text = lines[ann_end_line - 1]
                ann_end_col = _byte_col_to_char_col(ann_end_line_text, ann.end_col_offset or (ann.col_offset + 1))
                removals.append((maybe_arg.lineno, name_end_col, ann_end_line, ann_end_col))

    if not removals:
        return source_code

    # Sort removals in reverse order so later removals don't affect earlier offsets
    removals.sort(key=lambda r: (r[0], r[1]), reverse=True)

    for start_line, start_col, end_line, end_col in removals:
        if start_line == end_line:
            # Single-line removal
            line_idx = start_line - 1
            line = lines[line_idx]
            lines[line_idx] = line[:start_col] + line[end_col:]
        else:
            # Multi-line removal (rare but possible for complex annotations)
            first_idx = start_line - 1
            last_idx = end_line - 1
            lines[first_idx] = lines[first_idx][:start_col] + lines[last_idx][end_col:]
            del lines[first_idx + 1 : last_idx + 1]

    return "".join(lines)


# ============================================================================
# Dependencies reading
# ============================================================================


def read_dependencies(toml_path: Path) -> list[str]:
    """Read pip dependencies from a pyproject.toml or component TOML file."""
    with open(toml_path, "rb") as f:
        data = tomllib.load(f)
    # Standard pyproject.toml format
    deps = data.get("project", {}).get("dependencies", [])
    if deps:
        return list(deps)
    return []


# ============================================================================
# Code generation
# ============================================================================


def _build_argparse_code(spec: FunctionSpec) -> str:
    """Generate argparse wrapper code for the component function.

    Type-specific definitions (e.g. _deserialize_bool, import json) are placed
    right before 'import argparse', matching the Cloud-Pipelines SDK layout.
    """
    # Collect definitions needed by parameter types (deduplicated by content)
    definitions: dict[str, str] = {}
    for param in spec.inputs + spec.outputs:
        if param.tangle_type and param.tangle_type in _TYPE_DEFINITIONS:
            defn = _TYPE_DEFINITIONS[param.tangle_type]
            definitions[defn] = defn  # dedup by content

    # If there are return outputs, we need serializer helpers and json import
    has_return_outputs = len(spec.return_params) > 0
    if has_return_outputs:
        # Check if any return output needs json.dumps
        needs_json = any(
            _TYPE_TO_SERIALIZER.get(p.tangle_type or "String", "") == "json.dumps" for p in spec.return_params
        )
        if needs_json:
            definitions["import json"] = "import json"

    lines = sorted(definitions.values()) + [
        "import argparse",
        f"_parser = argparse.ArgumentParser(prog={repr(spec.component_name)}, "
        f"description={repr(spec.description or '')})",
    ]

    # Add arguments for all inputs and file-based outputs (OutputPath params)
    all_params = spec.inputs + spec.outputs
    for param in all_params:
        flag = "--" + param.yaml_name.replace("_", "-")
        is_required = param.kind == "output" or not param.optional
        line = (
            f'_parser.add_argument("{flag}", dest="{param.name}", '
            f"type={param.deserializer}, required={is_required}, "
            f"default=argparse.SUPPRESS)"
        )
        lines.append(line)

    # Add ----output-paths argument for NamedTuple return outputs
    if has_return_outputs:
        n = len(spec.return_params)
        lines.append(f'_parser.add_argument("----output-paths", dest="_output_paths", ' f"type=str, nargs={n})")

    lines.append("_parsed_args = vars(_parser.parse_args())")

    if has_return_outputs:
        lines.append('_output_files = _parsed_args.pop("_output_paths", [])')

    lines.append("")
    lines.append(f"_outputs = {spec.name}(**_parsed_args)")

    # Single return value (not NamedTuple) must be wrapped in a list
    # to be zipped with the serializers and output paths
    if has_return_outputs and spec.single_return_output:
        lines.append("_outputs = [_outputs]")

    # Add output serialization for return outputs
    if has_return_outputs:
        lines.append("")
        serializers = []
        for rp in spec.return_params:
            serializer = _TYPE_TO_SERIALIZER.get(rp.tangle_type or "String", "_serialize_str")
            serializers.append(f"    {serializer},")
        lines.append("_output_serializers = [")
        lines.extend(serializers)
        lines.append("]")
        lines.append("")
        lines.append("import os")
        lines.append("for idx, output_file in enumerate(_output_files):")
        lines.append("    try:")
        lines.append("        os.makedirs(os.path.dirname(output_file))")
        lines.append("    except OSError:")
        lines.append("        pass")
        lines.append("    with open(output_file, 'w') as f:")
        lines.append("        f.write(_output_serializers[idx](_outputs[idx]))")

    return "\n".join(lines)


def _build_args_section(spec: FunctionSpec) -> list[Any]:
    """Build the YAML args section with input/output placeholders."""
    args: list[Any] = []

    all_params = spec.inputs + spec.outputs
    for param in all_params:
        flag = "--" + param.yaml_name.replace("_", "-")

        # Determine the placeholder type
        if param.kind == "output":
            placeholder = {"outputPath": param.yaml_name}
        elif param.kind == "input_path":
            placeholder = {"inputPath": param.yaml_name}
        else:
            placeholder = {"inputValue": param.yaml_name}

        if param.optional:
            # Wrap in if/cond/isPresent/then for optional params
            args.append(
                {
                    "if": {
                        "cond": {"isPresent": param.yaml_name},
                        "then": [flag, placeholder],
                    }
                }
            )
        else:
            args.append(flag)
            args.append(placeholder)

    # Add ----output-paths entries for NamedTuple return outputs
    if spec.return_params:
        args.append("----output-paths")
        for rp in spec.return_params:
            args.append({"outputPath": rp.yaml_name})

    return args


def _build_pip_install_command(deps: list[str]) -> list[str]:
    """Build the pip install command prefix for the container."""
    if not deps:
        return []
    quoted = " ".join(repr(str(d)) for d in deps)
    install_cmd = (
        f"PIP_DISABLE_PIP_VERSION_CHECK=1 python3 -m pip install " f"--quiet --no-warn-script-location {quoted}"
    )
    return [
        "sh",
        "-c",
        f'({install_cmd} || {install_cmd} --user) && "$0" "$@"',
    ]


def _build_python_source(
    spec: FunctionSpec,
    mode: Literal["inline", "bundle"],
    bundled_modules_b64: str | None = None,
) -> str:
    """Build the full Python source code to embed in the YAML.

    For inline mode: helper functions + stripped source + argparse wrapper.
    For bundle mode: helper functions + sys.modules injection + stripped source + argparse wrapper.
    """
    parts: list[str] = []

    # Add _make_parent_dirs_and_return_path helper if needed
    has_output_path = any(p.kind == "output" for p in spec.params)
    if has_output_path:
        parts.append(_MAKE_PARENT_DIRS_HELPER)

    # Add _serialize_str helper if needed for NamedTuple return outputs
    if spec.return_params:
        needs_serialize_str = any(
            _TYPE_TO_SERIALIZER.get(p.tangle_type or "String", "_serialize_str") == "_serialize_str"
            for p in spec.return_params
        )
        if needs_serialize_str:
            parts.append(_SERIALIZE_STR_HELPER)

    # For bundle mode: add sys.modules injection from compressed embedded source text
    if mode == "bundle" and bundled_modules_b64:
        parts.append(ModuleBundler.build_injection(bundled_modules_b64))

    # Add the source code (type-hint-stripped)
    # Use full module source when available — this preserves helper functions defined
    # outside the target function, module-level imports, and constants.
    if spec.module_source_stripped:
        parts.append(spec.module_source_stripped)
    else:
        parts.append(spec.source_code_stripped)

    # Add argparse wrapper
    parts.append(_build_argparse_code(spec))

    full_source = "\n\n".join(parts)
    # Clean up consecutive blank lines
    full_source = re.sub(r"\n\n\n+", "\n\n", full_source).strip("\n") + "\n"
    return full_source


def _serialize_default(value: Any, tangle_type: str | None) -> str | None:
    """Serialize a default value to a string for YAML."""
    if value is inspect.Parameter.empty or value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, dict)):
        return json.dumps(value, sort_keys=True)
    return str(value)


# ============================================================================
# Component YAML building
# ============================================================================


def build_component_dict(
    spec: FunctionSpec,
    container_image: str,
    dependencies: list[str],
    annotations: dict[str, str],
    mode: Literal["inline", "bundle"] = "inline",
    bundled_modules_b64: str | None = None,
) -> dict[str, Any]:
    """Build the complete component YAML dict.

    Args:
        spec: Extracted function specification
        container_image: Docker image for the container
        dependencies: List of pip dependencies
        annotations: Metadata annotations dict
        mode: Generation mode
        bundled_modules_b64: Base64-encoded pickled modules (bundle mode only)

    Returns:
        Dict representing the full component YAML structure.
    """
    # Build inputs
    inputs = []
    for param in spec.inputs:
        input_spec: dict[str, Any] = {
            "name": param.yaml_name,
            "type": param.tangle_type,
        }
        if param.description:
            input_spec["description"] = param.description
        if param.default is not inspect.Parameter.empty and param.default is not None:
            serialized = _serialize_default(param.default, param.tangle_type)
            if serialized is not None:
                input_spec["default"] = serialized
        if param.optional:
            input_spec["optional"] = True
        inputs.append(input_spec)

    # Build outputs (OutputPath params + NamedTuple return fields)
    outputs = []
    for param in spec.all_outputs:
        output_spec: dict[str, Any] = {
            "name": param.yaml_name,
            "type": param.tangle_type,
        }
        if param.description:
            output_spec["description"] = param.description
        outputs.append(output_spec)

    # Build implementation
    all_deps = list(dependencies)

    pip_install = _build_pip_install_command(all_deps)
    python_source = _build_python_source(spec, mode, bundled_modules_b64)
    args = _build_args_section(spec)

    shell_bootstrap = textwrap.dedent("""\
        program_path=$(mktemp)
        printf "%s" "$0" > "$program_path"
        python3 -u "$program_path" "$@"
    """)

    command = pip_install + ["sh", "-ec", shell_bootstrap, python_source]

    # Tangle's schema rejects ``description: null``, so fall back to a generic
    # placeholder when the function has no docstring. Users can override by
    # adding a docstring to the function (its first paragraph becomes the
    # description — see ``extract_function_spec``).
    description = spec.description or f"{spec.component_name} component"

    component: dict[str, Any] = {
        "name": spec.component_name,
        "description": description,
    }

    if annotations:
        component["metadata"] = {"annotations": annotations}

    if inputs:
        component["inputs"] = inputs
    if outputs:
        component["outputs"] = outputs

    component["implementation"] = {
        "container": {
            "image": container_image,
            "command": command,
            "args": args,
        }
    }

    return component


# ============================================================================
# Top-level generation function
# ============================================================================


def generate_component_yaml(
    file_path: Path,
    output_path: Path,
    container_image: str,
    function_name: str | None = None,
    dependencies_from: Path | None = None,
    mode: Literal["inline", "bundle"] = "inline",
    custom_name: str | None = None,
    custom_annotations: dict[str, str] | None = None,
    strip_code: bool = False,
    strip_source_path: bool = False,
    resolve_root: Path | None = None,
    emit_generation_annotations: bool = True,
    path_annotation_mode: Literal["oss", "td_legacy"] = "oss",
) -> bool:
    """Generate a component YAML file from a Python function.

    Args:
        file_path: Path to the Python source file
        output_path: Where to write the generated YAML
        container_image: Docker image reference
        function_name: Function to extract (auto-detected if None)
        dependencies_from: Path to pyproject.toml with pip dependencies
        mode: "inline" for single-file, "bundle" for multi-file
        custom_name: Override the component name
        custom_annotations: Additional annotations to merge
        strip_code: Omit python_original_code annotation
        strip_source_path: Omit python_original_code_path annotation
        resolve_root: Root directory for resolving local module imports in bundle
            mode.  Defaults to ``file_path.parent``.  Set this when local modules
            live in sibling directories (e.g. ``src/utils`` alongside ``src/components``).
        emit_generation_annotations: Persist tangle-cli regeneration context
            annotations. Disable for downstream legacy snapshot compatibility.
        path_annotation_mode: ``"oss"`` always records source/YAML paths relative
            to their common ancestor. ``"td_legacy"`` only uses that relative
            common-root behavior inside a git checkout; outside git it records
            ``file_path.name`` / ``output_path.name`` to preserve the legacy
            downstream driver's historical basename-only snapshots.

    Returns:
        True on success, False on failure.
    """
    try:
        if path_annotation_mode not in {"oss", "td_legacy"}:
            raise ValueError("path_annotation_mode must be 'oss' or 'td_legacy'")

        # 1. Extract metadata from source (AST-based, before module loading)
        file_metadata, resolved_func_name = extract_file_metadata(file_path, function_name)
        if not resolved_func_name:
            raise ValueError(f"No public functions found in {file_path}")

        # 2. Load module and get function
        # Only add resolve_root to sys.path in bundle mode — in inline mode the
        # sibling modules won't be embedded, so letting the import succeed would
        # produce YAML that fails at runtime in the container.
        extra_paths = [resolve_root] if resolve_root and mode == "bundle" else None
        module = load_python_module(file_path, extra_sys_path=extra_paths)
        func = get_function_from_module(module, resolved_func_name)

        # 3. Extract interface, passing pre-computed metadata
        spec = extract_interface(func, docstring_metadata=file_metadata)
        if custom_name:
            spec.component_name = custom_name

        # Populate full module source (preserves helper functions, imports, constants)
        # Remove cloud_pipelines import since it's only used for type annotations
        module_source = file_path.read_text()
        lines = module_source.split("\n")
        lines = [
            line for line in lines if not (line.strip().startswith(("from cloud_pipelines", "import cloud_pipelines")))
        ]
        filtered_source = "\n".join(lines)
        filtered_source = _strip_main_guard(filtered_source)
        # Strip python-pipeline authoring imports + @task/@pipeline/@subpipeline
        # decorators so the baked runtime program does not re-run the authoring
        # decorator (which would turn the function into a CallableRef and crash).
        # Operates on module_source_stripped only; python_original_code stays
        # byte-verbatim (it is read separately from module_code below).
        filtered_source = _strip_authoring_constructs(filtered_source)
        spec.module_source_stripped = _strip_type_hints(filtered_source)

        # 3. Read dependencies
        deps: list[str] = []
        if dependencies_from:
            deps = read_dependencies(dependencies_from)

        # 4. Build annotations
        directory = file_path.parent.resolve()
        module_code = file_path.read_text()

        annotations: dict[str, str] = {
            "cloud_pipelines.net": "true",
            "components new regenerate python-function-component": "true",
        }
        if not strip_source_path:
            annotations["python_original_code_path"] = file_path.name
        if not strip_code:
            annotations["python_original_code"] = module_code

        # Add all docstring metadata to annotations (version, updated_at, custom keys)
        # Skip "name" and "description" since they're used for top-level fields, not annotations
        for key, value in spec.docstring_metadata.items():
            if key not in ("name", "description"):
                annotations[key] = value

        if deps:
            annotations["python_dependencies"] = json.dumps(deps)

        if emit_generation_annotations:
            annotations["tangle_cli_generation_function_name"] = resolved_func_name
            annotations["tangle_cli_generation_mode"] = mode

        # Use the common ancestor of source and output so both paths are clean
        # forward references (no ".."). This lets later local maintenance
        # commands find the source even when YAML is generated into a separate
        # output directory. Legacy (``td_legacy``) compatibility keeps
        # basename-only paths outside a git checkout to preserve historical
        # snapshots.
        resolved_source = file_path.resolve()
        resolved_output = output_path.resolve()
        common_dir = Path(os.path.commonpath([resolved_source, resolved_output]))
        git_root = get_git_root(directory)
        use_common_paths = path_annotation_mode == "oss" or git_root is not None

        def _path_annotation(path: Path) -> str:
            if use_common_paths:
                try:
                    return str(path.resolve().relative_to(common_dir))
                except ValueError:
                    return str(path)
            return path.name

        if not strip_source_path:
            annotations["python_original_code_path"] = _path_annotation(file_path)
        annotations["component_yaml_path"] = _path_annotation(output_path)
        if emit_generation_annotations:
            if dependencies_from:
                annotations["tangle_cli_generation_dependencies_from"] = _path_annotation(dependencies_from)
            if resolve_root:
                annotations["tangle_cli_generation_resolve_root"] = _path_annotation(resolve_root)

        # Git info — use the same common ancestor as git_relative_dir when common paths are active.
        if git_root:
            git_info = get_git_info(common_dir)
            git_info.pop("_git_root", None)
            # Override git_relative_dir to be the common ancestor
            try:
                git_info["git_relative_dir"] = str(common_dir.relative_to(git_root))
            except ValueError:
                pass
            annotations.update(git_info)
        else:
            git_info = get_git_info(directory)
            git_info.pop("_git_root", None)
            annotations.update(git_info)

        # Custom annotations
        if custom_annotations:
            annotations.update(custom_annotations)

        # Filter None values (annotation values must be strings)
        annotations = {k: v for k, v in annotations.items() if isinstance(v, str)}

        # 5. Handle bundle mode — embed source text of local modules
        # (not bytecode, which is Python-version-specific)
        bundled_modules_b64: str | None = None
        if mode == "bundle":
            module_sources = ModuleBundler.collect_sources(
                file_path,
                resolve_root=resolve_root,
                pip_deps=deps,
                source=spec.module_source_stripped,
            )
            if module_sources:
                bundled_modules_b64 = ModuleBundler.encode(module_sources)
                if bundled_modules_b64:
                    sorted_names = sorted(module_sources.keys(), key=lambda k: (k.count("."), k))
                    annotations["bundled_modules"] = json.dumps(sorted_names)

        # 6. Build and write YAML
        component = build_component_dict(
            spec=spec,
            container_image=container_image,
            dependencies=deps,
            annotations=annotations,
            mode=mode,
            bundled_modules_b64=bundled_modules_b64,
        )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            f.write(dump_yaml(component, width=120))

        return True

    except AuthoringStripError:
        # TaskEnv authoring-violation (§3.5): fail LOUD with the actionable
        # guidance instead of swallowing it into a warning + False. A silent
        # False would only resurface later as a confusing missing/broken
        # component at hydrate or backend run time, defeating the
        # "fail fast with a clear generator error" intent. Every OTHER failure
        # keeps the conservative warn + return False behaviour below.
        raise
    except Exception as e:
        warnings.warn(f"Error generating component YAML: {e}")
        return False
