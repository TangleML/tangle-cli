"""Generate the checked-in static Tangle API client pieces from OpenAPI.

Run from the repository root with:

    uv run python -m tangle_cli.openapi.codegen

The generator intentionally reuses :mod:`tangle_cli.api_schema` for operation
normalization so the offline client keeps the dynamic CLI/client expansion
semantics without requiring OpenAPI parsing at normal runtime.
"""

from __future__ import annotations

import argparse
import copy
import importlib
import json
import keyword
import os
import re
import sys
import tempfile
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .parser import DEFAULT_OPENAPI_PATH, load_openapi_schema, parsed_operations

_REPO_ROOT = Path(__file__).resolve().parents[5]
_GENERATED_DIR = _REPO_ROOT / "packages" / "tangle-api" / "src" / "tangle_api" / "generated"
DEFAULT_BACKEND_PATH = _REPO_ROOT / "third_party" / "tangle"
DEFAULT_OPERATIONS_CLASS_NAME = "GeneratedTangleApiOperations"
DEFAULT_MODEL_EXTENSION_MODULE = "tangle_cli.generated_model_extensions"
DEFAULT_MODEL_ALIASES: dict[str, tuple[str, ...]] = {
    "ComponentSpec": (
        "ComponentSpec-Output",
        "ComponentSpecOutput",
        "ComponentSpec-Input",
        "ComponentSpecInput",
    ),
}


def _safe_identifier(name: str) -> str:
    value = re.sub(r"\W", "_", name).strip("_").lower()
    value = re.sub(r"_+", "_", value) or "value"
    if value[0].isdigit():
        value = f"value_{value}"
    if keyword.iskeyword(value):
        value = f"{value}_"
    return value


def _class_name(name: str) -> str:
    parts = re.split(r"[^0-9A-Za-z]+", name)
    value = "".join(part[:1].upper() + part[1:] for part in parts if part)
    if not value:
        value = "GeneratedModel"
    if value[0].isdigit():
        value = f"Model{value}"
    return value


def _schema_ref_name(
    schema: dict[str, Any] | None,
    model_ref_aliases: dict[str, str] | None = None,
) -> str | None:
    if not schema:
        return None
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
        schema_name = ref.rsplit("/", 1)[1]
        return model_ref_aliases.get(schema_name, _class_name(schema_name)) if model_ref_aliases else _class_name(schema_name)
    for key in ("anyOf", "oneOf", "allOf"):
        for child in schema.get(key, []) or []:
            name = _schema_ref_name(child, model_ref_aliases=model_ref_aliases)
            if name:
                return name
    return None


def _success_response(operation: dict[str, Any]) -> dict[str, Any] | None:
    responses = operation.get("responses", {}) or {}
    for status in ("200", "201", "202", "204", "default"):
        response = responses.get(status)
        if response:
            break
    else:
        response = next(iter(responses.values()), None)
    return response if isinstance(response, dict) else None


def _success_schema(operation: dict[str, Any]) -> dict[str, Any] | None:
    response = _success_response(operation)
    if response is None:
        return None
    content = response.get("content", {}) or {}
    json_content = content.get("application/json") or next(iter(content.values()), {})
    schema = json_content.get("schema") if isinstance(json_content, dict) else None
    return schema if isinstance(schema, dict) else None


def _schema_type(schema: dict[str, Any]) -> str | None:
    schema_type = schema.get("type")
    if isinstance(schema_type, str):
        return schema_type
    if isinstance(schema_type, list):
        for item in schema_type:
            if item != "null":
                return str(item)
    return None


def _schema_allows_null(schema: dict[str, Any] | None) -> bool:
    if not schema:
        return False
    if schema.get("nullable") is True or schema.get("type") == "null":
        return True
    schema_type = schema.get("type")
    if isinstance(schema_type, list) and "null" in schema_type:
        return True
    for key in ("anyOf", "oneOf"):
        for child in schema.get(key, []) or []:
            if isinstance(child, dict) and _schema_allows_null(child):
                return True
    return False


def _response_model_name(
    operation: dict[str, Any],
    model_ref_aliases: dict[str, str] | None = None,
) -> str | None:
    schema = _success_schema(operation)
    if not schema:
        return None
    if _schema_type(schema) == "array":
        items = schema.get("items")
        return _schema_ref_name(items if isinstance(items, dict) else None, model_ref_aliases=model_ref_aliases)
    return _schema_ref_name(schema, model_ref_aliases=model_ref_aliases)


def _response_return_annotation(
    operation: dict[str, Any],
    model_ref_aliases: dict[str, str] | None = None,
) -> str:
    response = _success_response(operation)
    if response is None:
        return "Any"
    schema = _success_schema(operation)
    if schema is None or not schema:
        return "None"
    return _schema_return_annotation(schema, model_ref_aliases=model_ref_aliases)


def _schema_return_annotation(
    schema: dict[str, Any],
    model_ref_aliases: dict[str, str] | None = None,
) -> str:
    ref_name = _schema_ref_name(schema, model_ref_aliases=model_ref_aliases)
    if ref_name:
        return f"{ref_name} | None" if _schema_allows_null(schema) else ref_name

    schema_type = _schema_type(schema)
    if schema_type == "array":
        items = schema.get("items")
        item_ref = _schema_ref_name(items if isinstance(items, dict) else None, model_ref_aliases=model_ref_aliases)
        annotation = f"list[{item_ref}]" if item_ref else "list[Any]"
        return f"{annotation} | None" if _schema_allows_null(schema) else annotation

    primitives = {
        "string": "str",
        "integer": "int",
        "number": "float",
        "boolean": "bool",
    }
    if schema_type in primitives:
        annotation = primitives[schema_type]
        return f"{annotation} | None" if _schema_allows_null(schema) else annotation

    if schema_type == "object" or "properties" in schema or "additionalProperties" in schema:
        return "dict[str, Any]"

    return "Any"



def _parse_model_alias(value: str) -> tuple[str, tuple[str, ...]]:
    """Parse ``PublicModel=SourceModel[,OtherSource]`` alias config."""

    if "=" not in value:
        raise ValueError(
            "Model aliases must use PublicModel=SourceModel[,OtherSource] syntax"
        )
    alias_name, raw_sources = value.split("=", 1)
    alias_name = _class_name(alias_name.strip())
    _validate_class_name(alias_name)
    sources = tuple(source.strip() for source in raw_sources.split(",") if source.strip())
    if not sources:
        raise ValueError(f"Model alias {alias_name!r} must include at least one source schema")
    return alias_name, sources


def _model_alias_mapping(
    model_aliases: dict[str, Sequence[str] | str] | Sequence[str] | str | None,
) -> dict[str, tuple[str, ...]]:
    """Return public model aliases, applying built-in defaults first.

    A string or sequence uses CLI-style ``PublicModel=SourceModel`` entries.
    An empty-string entry disables the built-in defaults.
    """

    aliases = dict(DEFAULT_MODEL_ALIASES)
    if model_aliases is None:
        return aliases
    if isinstance(model_aliases, dict):
        for alias_name, sources in model_aliases.items():
            parsed_alias = _class_name(alias_name)
            _validate_class_name(parsed_alias)
            source_values = [sources] if isinstance(sources, str) else list(sources)
            source_tuple = tuple(str(source).strip() for source in source_values if str(source).strip())
            if not source_tuple:
                raise ValueError(f"Model alias {parsed_alias!r} must include at least one source schema")
            aliases[parsed_alias] = source_tuple
        return aliases

    values = [model_aliases] if isinstance(model_aliases, str) else list(model_aliases)
    if "" in values:
        aliases = {}
        values = [value for value in values if value != ""]
    for value in values:
        alias_name, sources = _parse_model_alias(value)
        aliases[alias_name] = sources
    return aliases


def _apply_model_aliases(
    schemas: dict[str, Any],
    model_aliases: dict[str, Sequence[str] | str] | Sequence[str] | str | None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Add alias schemas and return source schema -> public class aliases."""

    output = dict(schemas)
    existing_class_names = {_class_name(schema_name) for schema_name in output}
    model_ref_aliases: dict[str, str] = {}
    for alias_name, sources in _model_alias_mapping(model_aliases).items():
        present_sources = [source for source in sources if source in schemas]
        if not present_sources:
            continue
        if alias_name not in existing_class_names:
            output[alias_name] = dict(schemas[present_sources[0]])
            if isinstance(output[alias_name], dict):
                output[alias_name]["title"] = alias_name
            existing_class_names.add(alias_name)
        if alias_name in existing_class_names:
            for source in present_sources:
                model_ref_aliases[source] = alias_name
    return output, model_ref_aliases


def _request_body_schema_mapping(
    request_body_schemas: dict[str, dict[str, Any]] | Sequence[str] | str | None,
) -> dict[str, dict[str, Any]]:
    """Parse operation request-body schema overrides keyed by operation id."""

    if request_body_schemas is None:
        return {}
    if isinstance(request_body_schemas, dict):
        return {key: dict(value) for key, value in request_body_schemas.items()}

    values = [request_body_schemas] if isinstance(request_body_schemas, str) else list(request_body_schemas)
    overrides: dict[str, dict[str, Any]] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(
                "Request body schema overrides must use OperationId={...json schema...} syntax"
            )
        operation_id, raw_schema = value.split("=", 1)
        operation_id = operation_id.strip()
        if not operation_id:
            raise ValueError("Request body schema override operation id cannot be empty")
        try:
            schema = json.loads(raw_schema)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Request body schema override for {operation_id!r} is not valid JSON: {exc.msg}"
            ) from exc
        if not isinstance(schema, dict):
            raise ValueError(f"Request body schema override for {operation_id!r} must be a JSON object")
        overrides[operation_id] = schema
    return overrides


def _request_body_schema_file_mapping(values: Sequence[str] | str | None) -> dict[str, dict[str, Any]]:
    """Parse operation request-body schema overrides from JSON files."""

    if values is None:
        return {}
    raw_values = [values] if isinstance(values, str) else list(values)
    overrides: dict[str, dict[str, Any]] = {}
    for value in raw_values:
        if "=" not in value:
            raise ValueError(
                "Request body schema file overrides must use OperationId=path/to/schema.json syntax"
            )
        operation_id, raw_path = value.split("=", 1)
        operation_id = operation_id.strip()
        if not operation_id:
            raise ValueError("Request body schema file override operation id cannot be empty")
        path = Path(raw_path).expanduser()
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ValueError(f"Could not read request body schema file {path}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"Request body schema file {path} is not valid JSON: {exc.msg}") from exc
        if not isinstance(schema, dict):
            raise ValueError(f"Request body schema file {path} must contain a JSON object")
        overrides[operation_id] = schema
    return overrides


def _operation_override_keys(operation: Any) -> set[str]:
    """Return supported keys for request-body schema override matching."""

    operation_id = operation.operation.get("operationId")
    keys = {_method_name(operation.group_name, operation.command_name), operation.operation_name}
    if isinstance(operation_id, str) and operation_id:
        keys.add(operation_id)
        keys.add(_safe_identifier(operation_id))
    return keys


def _set_json_request_body_schema(operation: dict[str, Any], schema: dict[str, Any]) -> None:
    request_body = operation.setdefault("requestBody", {})
    content = request_body.setdefault("content", {})
    media = content.setdefault("application/json", {})
    media["schema"] = schema
    operation["x-tangle-cli-request-body-schema-override"] = True


def _apply_request_body_schema_overrides(
    schema: dict[str, Any],
    request_body_schemas: dict[str, dict[str, Any]] | Sequence[str] | str | None,
) -> dict[str, Any]:
    """Return schema with configured request-body schema overrides applied."""

    overrides = _request_body_schema_mapping(request_body_schemas)
    if not overrides:
        return schema

    output = copy.deepcopy(schema)
    operations = parsed_operations(output)
    remaining = dict(overrides)
    for operation in operations:
        matching_keys = _operation_override_keys(operation)
        for key in list(remaining):
            if key in matching_keys:
                _set_json_request_body_schema(operation.operation, remaining.pop(key))
    if remaining:
        raise ValueError(
            "Unknown request body schema override operation(s): " + ", ".join(sorted(remaining))
        )
    return output


@dataclass(frozen=True)
class _ModelExtensionRef:
    """Import reference for one generated model extension class."""

    module_name: str
    class_name: str
    alias: str


def _model_extension_modules(
    model_extension_module: str | Sequence[str] | None,
) -> list[str]:
    """Return ordered model extension modules, applying defaults first.

    ``None`` means the built-in default module. A string or sequence appends
    downstream modules after the built-in default. The empty-string sentinel
    disables the default module and is otherwise ignored.
    """

    if model_extension_module is None:
        modules: list[str] = []
    elif isinstance(model_extension_module, str):
        modules = [model_extension_module]
    else:
        modules = list(model_extension_module)

    include_default = True
    if "" in modules:
        include_default = False
        modules = [module for module in modules if module != ""]

    ordered = ([DEFAULT_MODEL_EXTENSION_MODULE] if include_default else []) + modules
    deduped: list[str] = []
    seen: set[str] = set()
    for module in ordered:
        if not module or module in seen:
            continue
        seen.add(module)
        deduped.append(module)
    return deduped


def _validate_module_name(module_name: str) -> str:
    parts = module_name.split(".")
    if not parts or any(not re.fullmatch(r"[A-Za-z_]\w*", part) or keyword.iskeyword(part) for part in parts):
        raise ValueError(f"Invalid model extension module name: {module_name!r}")
    return module_name


def _model_extension_mapping(module_name: str) -> dict[str, str]:
    """Load and validate a MODEL_EXTENSIONS mapping from an extension module."""

    module_name = _validate_module_name(module_name)
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:  # pragma: no cover - importlib preserves details
        raise ValueError(f"Could not import model extension module {module_name!r}: {exc}") from exc

    mapping = getattr(module, "MODEL_EXTENSIONS", None)
    if not isinstance(mapping, dict):
        raise ValueError(
            f"Model extension module {module_name!r} must define a MODEL_EXTENSIONS dict"
        )

    extensions: dict[str, str] = {}
    for model_name, extension_name in mapping.items():
        if not isinstance(model_name, str) or not isinstance(extension_name, str):
            raise ValueError("MODEL_EXTENSIONS keys and values must be strings")
        _validate_class_name(model_name)
        _validate_class_name(extension_name)
        if not hasattr(module, extension_name):
            raise ValueError(
                f"Model extension module {module_name!r} does not define {extension_name!r}"
            )
        extensions[model_name] = extension_name
    return extensions


def _model_extension_refs(
    model_extension_module: str | Sequence[str] | None,
) -> dict[str, list[_ModelExtensionRef]]:
    """Resolve model extension refs by generated class in configured order."""

    refs_by_model: dict[str, list[_ModelExtensionRef]] = {}
    raw_refs: list[_ModelExtensionRef] = []
    for module_name in _model_extension_modules(model_extension_module):
        for model_name, extension_name in _model_extension_mapping(module_name).items():
            ref = _ModelExtensionRef(
                module_name=module_name,
                class_name=extension_name,
                alias=extension_name,
            )
            refs_by_model.setdefault(model_name, []).append(ref)
            raw_refs.append(ref)

    unique_ref_keys: list[tuple[str, str]] = []
    seen_ref_keys: set[tuple[str, str]] = set()
    for ref in raw_refs:
        key = (ref.module_name, ref.class_name)
        if key in seen_ref_keys:
            continue
        seen_ref_keys.add(key)
        unique_ref_keys.append(key)

    class_name_counts = Counter(class_name for _, class_name in unique_ref_keys)
    alias_counts: Counter[str] = Counter()
    aliases_by_ref: dict[tuple[str, str], str] = {}
    for module_name, class_name in unique_ref_keys:
        if class_name_counts[class_name] == 1:
            alias = class_name
        else:
            alias_base = f"_{_safe_identifier(module_name)}_{class_name}"
            alias_counts[alias_base] += 1
            alias = alias_base if alias_counts[alias_base] == 1 else f"{alias_base}_{alias_counts[alias_base]}"
        aliases_by_ref[(module_name, class_name)] = alias

    aliased: dict[str, list[_ModelExtensionRef]] = {}
    for model_name, refs in refs_by_model.items():
        aliased[model_name] = []
        for ref in refs:
            aliased[model_name].append(
                _ModelExtensionRef(
                    module_name=ref.module_name,
                    class_name=ref.class_name,
                    alias=aliases_by_ref[(ref.module_name, ref.class_name)],
                )
            )
    return aliased


def _model_extension_import_lines(refs_by_model: dict[str, list[_ModelExtensionRef]]) -> list[str]:
    """Render deterministic import lines for configured model extensions."""

    refs_by_module: dict[str, list[_ModelExtensionRef]] = {}
    for refs in refs_by_model.values():
        for ref in refs:
            refs_by_module.setdefault(ref.module_name, []).append(ref)

    lines: list[str] = []
    for module_name, refs in sorted(refs_by_module.items()):
        imports: list[str] = []
        seen: set[tuple[str, str]] = set()
        for ref in sorted(refs, key=lambda item: (item.class_name, item.alias)):
            key = (ref.class_name, ref.alias)
            if key in seen:
                continue
            seen.add(key)
            if ref.alias == ref.class_name:
                imports.append(ref.class_name)
            else:
                imports.append(f"{ref.class_name} as {ref.alias}")
        lines.append(f"from {module_name} import {', '.join(imports)}")
    return lines


def generate_models(
    schema: dict[str, Any],
    model_extension_module: str | Sequence[str] | None = None,
    model_aliases: dict[str, Sequence[str] | str] | Sequence[str] | str | None = None,
) -> str:
    """Generate Pydantic model classes and apply configured model extensions."""

    raw_schemas = schema.get("components", {}).get("schemas", {}) or {}
    schemas, _ = _apply_model_aliases(raw_schemas, model_aliases)
    extension_refs = _model_extension_refs(model_extension_module)
    lines: list[str] = [
        '"""Generated Pydantic models for the checked-in Tangle OpenAPI schema.\n\nDo not edit by hand; run ``uv run python -m tangle_cli.openapi.codegen``.\n"""',
        "",
        "from __future__ import annotations",
        "",
        "from typing import Any",
        "",
        "from pydantic import Field",
        "",
        "from tangle_cli.generated_runtime import TangleGeneratedModel",
        "",
    ]

    generated_class_names = {
        _class_name(schema_name)
        for schema_name, schema_def in schemas.items()
        if isinstance(schema_def, dict)
        and (schema_def.get("type") in {"object", None} or "properties" in schema_def)
    }
    used_extensions = {
        class_name: extension_refs[class_name]
        for class_name in sorted(generated_class_names)
        if class_name in extension_refs
    }
    imports = _model_extension_import_lines(used_extensions)
    if imports:
        lines.extend(imports)
        lines.append("")

    exports: list[str] = []
    for schema_name, schema_def in sorted(schemas.items(), key=lambda item: _class_name(item[0])):
        class_name = _class_name(schema_name)
        exports.append(class_name)
        if not isinstance(schema_def, dict) or schema_def.get("type") not in {"object", None} and "properties" not in schema_def:
            lines.extend([f"{class_name} = Any", ""])
            continue
        properties = schema_def.get("properties") or {}
        extension_refs_for_class = used_extensions.get(class_name, [])
        generated_base_name = f"_{class_name}Generated"
        lines.extend([f"class {generated_base_name}(TangleGeneratedModel):"])
        if not properties:
            lines.append("    pass")
        else:
            for prop_name in sorted(properties):
                field_name = _safe_identifier(prop_name)
                if field_name != prop_name:
                    lines.append(f"    {field_name}: Any = Field(default=None, alias={prop_name!r})")
                else:
                    lines.append(f"    {field_name}: Any = None")
        lines.append("")
        extension_bases = [ref.alias for ref in reversed(extension_refs_for_class)]
        bases = extension_bases + [generated_base_name]
        lines.extend([
            f"class {class_name}({', '.join(bases)}):",
            "    pass",
            "",
        ])

    lines.append(f"__all__ = {exports!r}")
    lines.append("")
    return "\n".join(lines)

def _method_name(group_name: str, command_name: str) -> str:
    return f"{_safe_identifier(group_name)}_{_safe_identifier(command_name)}"


def _validate_class_name(name: str) -> str:
    """Validate a generated class name or extension class name."""

    if not re.fullmatch(r"[A-Za-z_]\w*", name) or keyword.iskeyword(name):
        raise ValueError(f"Invalid generated operations class name: {name!r}")
    return name


def _param_signature(
    parameters: list[Any],
    has_request_body: bool,
    *,
    raw_body_override: bool = False,
) -> tuple[str, list[str], list[str], list[str], bool]:
    required: list[Any] = []
    optional: list[Any] = []
    for parameter in parameters:
        (required if parameter.required else optional).append(parameter)
    ordered = required + optional
    seen: set[str] = set()
    signature_parts: list[str] = []
    path_names: list[str] = []
    query_names: list[str] = []
    body_names: list[str] = []
    for parameter in ordered:
        name = _safe_identifier(parameter.local_name)
        if name in seen:
            continue
        seen.add(name)
        if parameter.required:
            signature_parts.append(f"{name}: Any")
        else:
            signature_parts.append(f"{name}: Any = None")
        if parameter.location == "path":
            path_names.append(name)
        elif parameter.location == "query":
            query_names.append(name)
        elif parameter.location == "body":
            body_names.append(name)
    include_body = has_request_body and not body_names
    if include_body:
        body_annotation = "dict[str, Any] | None" if raw_body_override else "Any"
        signature_parts.append(f"body: {body_annotation} = None")
    return ", ".join(signature_parts), path_names, query_names, body_names, include_body


def _dict_literal(names: list[str]) -> str:
    if not names:
        return "None"
    return "{" + ", ".join(f"{name!r}: {name}" for name in names) + "}"


def _validate_operation_path(path: str) -> None:
    """Reject OpenAPI operation paths that could override the configured origin."""

    parsed_path = urllib.parse.urlparse(path)
    if parsed_path.scheme or parsed_path.netloc:
        raise ValueError(f"OpenAPI operation path must be relative: {path!r}")


def generate_operations(
    schema: dict[str, Any],
    operations_class_name: str = DEFAULT_OPERATIONS_CLASS_NAME,
    model_aliases: dict[str, Sequence[str] | str] | Sequence[str] | str | None = None,
    request_body_schemas: dict[str, dict[str, Any]] | Sequence[str] | str | None = None,
) -> str:
    """Generate the static operation mixin class for parsed OpenAPI operations."""

    operations_class_name = _validate_class_name(operations_class_name)
    schema = _apply_request_body_schema_overrides(schema, request_body_schemas)
    operations = parsed_operations(schema)
    _, model_ref_aliases = _apply_model_aliases(
        schema.get("components", {}).get("schemas", {}) or {},
        model_aliases,
    )
    response_models = sorted({name for op in operations if (name := _response_model_name(op.operation, model_ref_aliases))})
    imports = ", ".join(response_models)
    lines: list[str] = [
        '"""Generated static endpoint methods for the Tangle API.\n\nDo not edit by hand; run ``uv run python -m tangle_cli.openapi.codegen``.\n"""',
        "",
        "from __future__ import annotations",
        "",
        "from collections.abc import Mapping",
        "from typing import TYPE_CHECKING, Any",
        "",
    ]
    if imports:
        lines.extend([f"from .models import {imports}", ""])

    lines.extend([
        "",
        f"class {operations_class_name}:",
        "    \"\"\"Generated checked-in methods for Tangle API operations.\"\"\"",
        "",
        "    if TYPE_CHECKING:",
        "        def _request_json(",
        "            self,",
        "            method: str,",
        "            path: str,",
        "            *,",
        "            path_params: Mapping[str, Any] | None = None,",
        "            params: Mapping[str, Any] | None = None,",
        "            json_data: Any = None,",
        "            response_model: Any = None,",
        "        ) -> Any: ...",
        "",
    ])

    used_methods: set[str] = set()
    for operation in operations:
        _validate_operation_path(operation.path)
        method_name = _method_name(operation.group_name, operation.command_name)
        if method_name in used_methods:
            raise RuntimeError(f"duplicate generated method {method_name}")
        used_methods.add(method_name)
        signature, path_names, query_names, body_names, include_body = _param_signature(
            list(operation.parameters),
            operation.has_request_body,
            raw_body_override=bool(operation.operation.get("x-tangle-cli-request-body-schema-override")),
        )
        response_model = _response_model_name(operation.operation, model_ref_aliases)
        response_arg = response_model if response_model else "None"
        response_annotation = _response_return_annotation(operation.operation, model_ref_aliases)
        if signature:
            def_line = f"    def {method_name}(self, {signature}) -> {response_annotation}:"
        else:
            def_line = f"    def {method_name}(self) -> {response_annotation}:"
        lines.extend([
            def_line,
            f"        return self._request_json(",
            f"            {operation.method.upper()!r},",
            f"            {operation.path!r},",
            f"            path_params={_dict_literal(path_names)},",
            f"            params={_dict_literal(query_names)},",
        ])
        if body_names:
            lines.append(f"            json_data={_dict_literal(body_names)},")
        elif include_body:
            lines.append("            json_data=body,")
        else:
            lines.append("            json_data=None,")
        lines.extend([
            f"            response_model={response_arg},",
            "        )",
            "",
        ])

    lines.append(f"__all__ = {[operations_class_name]!r}")
    lines.append("")
    return "\n".join(lines)


def update_openapi_from_url(
    openapi_url: str,
    *,
    destination: str | Path = DEFAULT_OPENAPI_PATH,
) -> Path:
    """Fetch a remote OpenAPI JSON document and write it to *destination*."""

    request = urllib.request.Request(openapi_url, headers={"User-Agent": "tangle-cli-codegen"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read()
    schema = json.loads(payload.decode("utf-8"))
    return write_openapi_schema(schema, destination)


def update_openapi_from_backend(
    *,
    backend_path: str | Path = DEFAULT_BACKEND_PATH,
    destination: str | Path = DEFAULT_OPENAPI_PATH,
    database_uri: str | None = None,
) -> Path:
    """Import the official backend FastAPI app and write its OpenAPI schema."""

    schema = load_openapi_from_backend(backend_path, database_uri=database_uri)
    return write_openapi_schema(schema, destination)


def load_openapi_from_backend(
    backend_path: str | Path = DEFAULT_BACKEND_PATH,
    *,
    database_uri: str | None = None,
) -> dict[str, Any]:
    """Return ``api_server_main.app.openapi()`` from a backend checkout.

    The backend creates a database engine at import time, so codegen points it at
    a temporary SQLite database unless an explicit URI is supplied.
    """

    backend_dir = Path(backend_path).resolve()
    if not (backend_dir / "api_server_main.py").exists():
        raise FileNotFoundError(f"{backend_dir} does not contain api_server_main.py")

    old_path = list(sys.path)
    old_database_uri = os.environ.get("DATABASE_URI")
    old_database_url = os.environ.get("DATABASE_URL")
    old_modules = {
        name: module
        for name, module in sys.modules.items()
        if name == "api_server_main" or name.startswith("cloud_pipelines_backend")
    }
    for name in list(old_modules):
        sys.modules.pop(name, None)

    with tempfile.TemporaryDirectory(prefix="tangle-openapi-codegen-") as tmpdir:
        os.environ["DATABASE_URI"] = database_uri or f"sqlite:///{Path(tmpdir) / 'openapi_codegen.sqlite'}"
        os.environ.pop("DATABASE_URL", None)
        sys.path.insert(0, str(backend_dir))
        try:
            api_server_main = importlib.import_module("api_server_main")
            schema = api_server_main.app.openapi()
        finally:
            sys.path[:] = old_path
            for name in [
                name
                for name in sys.modules
                if name == "api_server_main" or name.startswith("cloud_pipelines_backend")
            ]:
                sys.modules.pop(name, None)
            sys.modules.update(old_modules)
            if old_database_uri is None:
                os.environ.pop("DATABASE_URI", None)
            else:
                os.environ["DATABASE_URI"] = old_database_uri
            if old_database_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = old_database_url

    if not isinstance(schema, dict) or "paths" not in schema:
        raise ValueError(f"Backend at {backend_dir} did not produce an OpenAPI paths object")
    return schema


def write_openapi_schema(schema: dict[str, Any], destination: str | Path = DEFAULT_OPENAPI_PATH) -> Path:
    """Write *schema* as the checked-in OpenAPI snapshot."""

    if not isinstance(schema, dict) or "paths" not in schema:
        raise ValueError("OpenAPI schema did not contain paths")
    destination_path = Path(destination)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    destination_path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination_path


def generate(
    openapi_path: str | Path = DEFAULT_OPENAPI_PATH,
    generated_dir: str | Path = _GENERATED_DIR,
    *,
    operations_class_name: str = DEFAULT_OPERATIONS_CLASS_NAME,
    model_extension_module: str | Sequence[str] | None = None,
    model_aliases: dict[str, Sequence[str] | str] | Sequence[str] | str | None = None,
    request_body_schemas: dict[str, dict[str, Any]] | Sequence[str] | str | None = None,
) -> tuple[dict[str, Any], list[Path]]:
    schema = load_openapi_schema(openapi_path)
    output_dir = Path(generated_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_files = [
        output_dir / "__init__.py",
        output_dir / "models.py",
        output_dir / "operations.py",
    ]
    generated_files[0].write_text(
        '"""Generated OpenAPI support modules."""\n',
        encoding="utf-8",
    )
    generated_files[1].write_text(
        generate_models(
            schema,
            model_extension_module=model_extension_module,
            model_aliases=model_aliases,
        ),
        encoding="utf-8",
    )
    generated_files[2].write_text(
        generate_operations(
            schema,
            operations_class_name=operations_class_name,
            model_aliases=model_aliases,
            request_body_schemas=request_body_schemas,
        ),
        encoding="utf-8",
    )
    return schema, generated_files


def _display_path(path: str | Path) -> str:
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


def _print_summary(
    *,
    source: str,
    openapi_path: str | Path,
    generated_files: list[Path],
    schema: dict[str, Any],
    wrote_openapi: bool,
) -> None:
    print(f"Loaded OpenAPI from {source}")
    if wrote_openapi:
        print(f"Wrote {_display_path(openapi_path)}")
    for path in generated_files:
        print(f"Wrote {_display_path(path)}")
    print(f"Generated {len(parsed_operations(schema))} operations from {len(schema.get('paths', {}))} paths")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openapi", default=str(DEFAULT_OPENAPI_PATH), help="Path to openapi.json")
    parser.add_argument(
        "--out",
        default=str(_GENERATED_DIR),
        help="Generated support module directory (default: packages/tangle-api/src/tangle_api/generated).",
    )
    parser.add_argument(
        "--operations-class-name",
        default=DEFAULT_OPERATIONS_CLASS_NAME,
        help=(
            "Class name to generate in operations.py "
            f"(default: {DEFAULT_OPERATIONS_CLASS_NAME})."
        ),
    )
    parser.add_argument(
        "--model-extension-module",
        action="append",
        default=None,
        help=(
            "Importable module containing a MODEL_EXTENSIONS mapping from "
            "generated model class names to extension class names. Repeat to "
            "compose modules in order; later modules override earlier ones. "
            "The built-in default module is applied first unless an empty string "
            "is passed to disable it. "
            f"(default first: {DEFAULT_MODEL_EXTENSION_MODULE})."
        ),
    )
    parser.add_argument(
        "--model-alias",
        action="append",
        default=None,
        help=(
            "Expose a stable public model class from one or more source schemas, "
            "using PublicModel=SourceSchema[,OtherSourceSchema]. Repeat for "
            "multiple aliases. The built-in ComponentSpec alias is applied first "
            "unless an empty string is passed to disable defaults."
        ),
    )
    parser.add_argument(
        "--request-body-schema",
        action="append",
        default=None,
        help=(
            "Override an operation JSON request-body schema using "
            "OperationId={...json schema...}. OperationId may be the OpenAPI "
            "operationId, generated method name, or group.command name. Repeat "
            "for multiple operations."
        ),
    )
    parser.add_argument(
        "--request-body-schema-file",
        action="append",
        default=None,
        help=(
            "Override an operation JSON request-body schema from a JSON file "
            "using OperationId=path/to/schema.json. Repeat for multiple operations."
        ),
    )
    parser.add_argument(
        "--openapi-url",
        default=None,
        help="Remote OpenAPI JSON URL to fetch before regenerating.",
    )
    parser.add_argument(
        "--backend-path",
        default=None,
        help=(
            "Backend checkout/submodule path to import for OpenAPI generation "
            f"(default: {_display_path(DEFAULT_BACKEND_PATH)})."
        ),
    )
    parser.add_argument(
        "--backend-database-uri",
        default=None,
        help="Database URI used while importing the backend app; defaults to a temporary SQLite DB.",
    )
    parser.add_argument(
        "--from-snapshot",
        action="store_true",
        help="Regenerate support modules from the existing local openapi.json snapshot.",
    )
    args = parser.parse_args(argv)
    try:
        _validate_class_name(args.operations_class_name)
        _model_extension_refs(args.model_extension_module)
        _model_alias_mapping(args.model_alias)
        request_body_schema_overrides = _request_body_schema_mapping(args.request_body_schema)
        request_body_schema_overrides.update(_request_body_schema_file_mapping(args.request_body_schema_file))
        if not request_body_schema_overrides:
            request_body_schema_overrides = None
    except ValueError as exc:
        parser.error(str(exc))
    source_count = sum(bool(value) for value in (args.openapi_url, args.backend_path, args.from_snapshot))
    if source_count > 1:
        parser.error("choose only one OpenAPI source: --openapi-url, --backend-path, or --from-snapshot")

    wrote_openapi = False
    if args.openapi_url:
        update_openapi_from_url(args.openapi_url, destination=args.openapi)
        source = f"URL: {args.openapi_url}"
        wrote_openapi = True
    elif args.from_snapshot:
        source = f"snapshot: {_display_path(args.openapi)}"
    else:
        backend_path = Path(args.backend_path) if args.backend_path else DEFAULT_BACKEND_PATH
        if not (backend_path / "api_server_main.py").exists():
            if args.backend_path:
                parser.exit(1, f"Backend source not found: {_display_path(backend_path)}\n")
            parser.exit(
                1,
                "Default backend submodule not found. Run: git submodule update --init --recursive\n",
            )
        update_openapi_from_backend(
            backend_path=backend_path,
            destination=args.openapi,
            database_uri=args.backend_database_uri,
        )
        source = f"backend: {_display_path(backend_path)}"
        wrote_openapi = True

    schema, generated_files = generate(
        args.openapi,
        args.out,
        operations_class_name=args.operations_class_name,
        model_extension_module=args.model_extension_module,
        model_aliases=args.model_alias,
        request_body_schemas=request_body_schema_overrides,
    )
    _print_summary(
        source=source,
        openapi_path=args.openapi,
        generated_files=generated_files,
        schema=schema,
        wrote_openapi=wrote_openapi,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
