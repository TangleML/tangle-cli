"""Standalone validation helpers for the *dehydrated* pipeline schema.

This module is intentionally decoupled from the hydrator: it only loads
the packaged ``dehydrated_pipeline_schema.json`` and offers pure helpers
the compiler (and tests) can call. It does NOT change any existing
``PipelineHydrator`` behavior.

Public surface:

* :func:`load_dehydrated_schema` — load (and cache) the packaged schema.
* :func:`validate_dehydrated_data` — JSON-Schema (Draft 2020-12)
  validation, raising :class:`SchemaValidationError` with the single
  best/most-specific message.
* :func:`iter_template_delimiters` / :func:`assert_no_template_delimiters`
  — generic "no template delimiters" output contract: the compiled
  output must contain no ``{{``, ``{%`` or ``{#`` in any string. This is
  generic (not SQL/Jinja aware): a compiled dehydrated pipeline carries
  final rendered values only.
* :func:`is_dehydrated_pipeline` — shape detector (no raise): top-level
  ``name`` + ``implementation.graph.tasks``, no ``template_file``, and
  task ``arguments`` values that are raw string constants or ``graphInput``
  / ``taskOutput`` wrappers.
* :func:`validate_dehydrated_pipeline` — JSON-Schema validation PLUS the
  deeper semantic checks jsonschema cannot express cleanly (dangling
  ``taskOutput.taskId``, undeclared ``graphInput.inputName``,
  ``outputValues`` ↔ ``outputs`` correspondence, scalar metadata
  annotations, pure componentRefs) and the no-template-delimiter scan.

Everything here is standalone — it never changes ``PipelineHydrator``
behavior. ``compile_pipeline`` uses :func:`validate_dehydrated_pipeline`
for its richer pre-write check.
"""
from __future__ import annotations

import json
from collections.abc import Collection, Iterator, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema
from jsonschema.validators import validator_for

# Template delimiters that must never appear in a compiled dehydrated
# pipeline's strings. The compiled output is the final, rendered form;
# any surviving delimiter means an upstream template was not rendered.
_TEMPLATE_DELIMITERS = ("{{", "{%", "{#")

_SCHEMA_FILENAME = "dehydrated_pipeline_schema.json"


class SchemaValidationError(ValueError):
    """Raised when a dehydrated pipeline fails schema/contract validation.

    The compiler wraps this in a ``CompileError`` so the CLI exits 1 with
    a friendly message; tests may assert on it directly.
    """


def _schema_path() -> Path:
    """Locate the packaged dehydrated schema JSON.

    Prefers :mod:`importlib.resources` so it resolves when ``tangle_cli``
    is installed as a wheel (``schemas/*`` is declared package data). Falls
    back to a path next to this module for editable / source checkouts.
    """
    try:
        from importlib.resources import files

        resource = files("tangle_cli") / "schemas" / _SCHEMA_FILENAME
        # ``as_file`` would be needed for zip imports, but tangle_cli is
        # always installed unzipped; a direct filesystem path is fine here.
        candidate = Path(str(resource))
        if candidate.exists():
            return candidate
    except (ModuleNotFoundError, FileNotFoundError, TypeError):  # pragma: no cover
        pass
    # Fallback: alongside this module.
    return Path(__file__).parent / "schemas" / _SCHEMA_FILENAME


@lru_cache(maxsize=1)
def load_dehydrated_schema() -> dict[str, Any]:
    """Load and cache the packaged dehydrated pipeline JSON schema."""
    path = _schema_path()
    try:
        return json.loads(path.read_text())
    except FileNotFoundError as e:  # pragma: no cover — packaging error
        raise SchemaValidationError(
            f"dehydrated pipeline schema not found at {path}"
        ) from e


def validate_dehydrated_data(data: Mapping[str, Any]) -> None:
    """Validate ``data`` against the dehydrated pipeline schema.

    Uses the draft declared in the schema's ``$schema`` (Draft 2020-12)
    via :func:`jsonschema.validators.validator_for`.

    Raises:
        SchemaValidationError: with the single best/most-specific message
            when ``data`` does not conform.
    """
    schema = load_dehydrated_schema()
    validator_cls = validator_for(schema)
    validator_cls.check_schema(schema)
    validator = validator_cls(schema)

    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
    if not errors:
        return

    best = jsonschema.exceptions.best_match(errors)
    if best is None:  # pragma: no cover — errors is non-empty here
        best = errors[0]
    location = (
        ".".join(str(p) for p in best.absolute_path)
        if best.absolute_path
        else "root"
    )
    raise SchemaValidationError(
        f"dehydrated pipeline failed schema validation at {location}: "
        f"{best.message}"
    )


def iter_template_delimiters(data: Any, _path: str = "") -> Iterator[tuple[str, str]]:
    """Yield ``(json_path, delimiter)`` for every string containing a
    template delimiter, walking ``data`` recursively.

    Generic scan: keys and values of mappings and items of sequences are
    all inspected. The scan is operation-agnostic — it knows nothing
    about SQL or Jinja semantics, only the three delimiter tokens.
    """
    if isinstance(data, str):
        for delim in _TEMPLATE_DELIMITERS:
            if delim in data:
                yield _path or "root", delim
        return
    if isinstance(data, Mapping):
        for key, value in data.items():
            key_path = f"{_path}.{key}" if _path else str(key)
            # Inspect the key itself too — keys are emitted verbatim.
            if isinstance(key, str):
                for delim in _TEMPLATE_DELIMITERS:
                    if delim in key:
                        yield f"{key_path} (key)", delim
            yield from iter_template_delimiters(value, key_path)
        return
    if isinstance(data, (list, tuple)):
        for i, item in enumerate(data):
            item_path = f"{_path}[{i}]"
            yield from iter_template_delimiters(item, item_path)
        return


def assert_no_template_delimiters(
    data: Mapping[str, Any],
    exempt_paths: Collection[str] = (),
) -> None:
    """Assert the compiled output contains no template delimiters.

    Args:
        data: the compiled (dehydrated) pipeline dict to scan.
        exempt_paths: JSON paths (in the dot-delimited form
            :func:`iter_template_delimiters` yields) whose delimiters are a
            legitimate RUNTIME placeholder and must NOT fail the guard —
            e.g. a ``run-query`` ``sql_query`` carrying a ``{{input_1}}``
            sentinel the op substitutes at run time, authored via
            :func:`tangle_cli.python_pipeline.raw`. An offender at one of
            these exact paths is skipped; every OTHER delimiter still fails,
            so real compile-time template leaks are still caught. Defaults
            to no exemptions, so existing callers are unaffected.

    Raises:
        SchemaValidationError: listing the offending locations when any
            non-exempt string contains ``{{``, ``{%`` or ``{#``.
    """
    allowed = set(exempt_paths)
    offenders = [
        (path, delim)
        for path, delim in iter_template_delimiters(data)
        if path not in allowed
    ]
    if not offenders:
        return
    detail = "; ".join(f"{path} contains {delim!r}" for path, delim in offenders)
    raise SchemaValidationError(
        "compiled pipeline output must contain no template delimiters "
        "({{, {% or {#}) — the dehydrated output carries final rendered "
        f"values only. Offending location(s): {detail}. Render any "
        "templated value in your pipeline code before passing it as a "
        "task argument, or wrap a genuine runtime placeholder in "
        "tangle_cli.python_pipeline.raw(...) to mark it intentional."
    )


# ---------------------------------------------------------------------------
# Phase 5: dehydrated-pipeline shape detection + semantic validation.
#
# These helpers are standalone. They never touch PipelineHydrator behavior.

# The ONLY top-level keys a dehydrated pipeline may carry. Compile-time
# config is baked into raw string constants and never emitted, so anything
# else at the top level is a leaked config key.
_ALLOWED_TOP_LEVEL_KEYS = frozenset(
    {"name", "description", "metadata", "inputs", "outputs", "implementation"}
)

# The reference-only ArgumentValue wrappers. A constant is NOT a wrapper —
# it is a raw string (matching the runnable Tangle argument contract).
_REFERENCE_ARGUMENT_KEYS = ("graphInput", "taskOutput")


def _is_argument_value(value: Any) -> bool:
    """True when ``value`` looks like a runnable ArgumentValue — a raw
    string constant, or a mapping carrying a ``graphInput`` / ``taskOutput``
    wrapper.

    There is no ambiguity: a raw string constant (even one whose text is
    ``"graphInput"`` or JSON like ``'{"graphInput": ...}'``) is a string,
    never the object wrapper shapes — so it can never collide with a
    ``graphInput`` / ``taskOutput`` mapping.
    """
    if isinstance(value, str):
        return True
    return isinstance(value, Mapping) and any(
        key in value for key in _REFERENCE_ARGUMENT_KEYS
    )


def is_dehydrated_pipeline(data: Any) -> bool:
    """Detect a *dehydrated* pipeline by shape (never raises).

    A dehydrated pipeline has:

    * a top-level ``name`` and ``implementation.graph.tasks`` (non-empty);
    * NO ``template_file`` (it is the final rendered form, not a wrapper);
    * task ``arguments`` values AND graph ``outputValues`` values (when
      present) that are raw string constants or ``graphInput`` /
      ``taskOutput`` wrappers — a non-string raw value (a bare
      number/list/object) or a legacy ``{constantValue: ...}`` wrapper is
      not a runnable argument value, so it means the input is not yet
      dehydrated.

    This is intentionally lenient about everything else (it is a detector,
    not a validator); use :func:`validate_dehydrated_pipeline` for strict
    checking.
    """
    if not isinstance(data, Mapping):
        return False
    if "template_file" in data:
        return False
    if "name" not in data:
        return False

    implementation = data.get("implementation")
    if not isinstance(implementation, Mapping):
        return False
    graph = implementation.get("graph")
    if not isinstance(graph, Mapping):
        return False
    tasks = graph.get("tasks")
    if not isinstance(tasks, Mapping) or not tasks:
        return False

    for task in tasks.values():
        if not isinstance(task, Mapping):
            return False
        arguments = task.get("arguments")
        if arguments is None:
            continue
        if not isinstance(arguments, Mapping):
            return False
        for value in arguments.values():
            if not _is_argument_value(value):
                return False

    # Graph outputValues use the SAME runnable argument-value contract, so a
    # legacy ``{constantValue: ...}`` (or any non-string raw value) there must
    # not pass the detector either.
    output_values = graph.get("outputValues")
    if output_values is not None:
        if not isinstance(output_values, Mapping):
            return False
        for value in output_values.values():
            if not _is_argument_value(value):
                return False
    return True


def _declared_names(specs: Any) -> set[str]:
    """Collect the ``name`` values from an inputs/outputs spec list."""
    names: set[str] = set()
    if isinstance(specs, list):
        for spec in specs:
            if isinstance(spec, Mapping) and isinstance(spec.get("name"), str):
                names.add(spec["name"])
    return names


def _assert_pure_component_ref(component_ref: Any, loc: str) -> None:
    """Assert a componentRef is a PURE ref — no inline ``spec`` / ``text``.

    Redundant with the schema's ``not`` clause, but kept as an explicit,
    clearly-messaged semantic check.
    """
    if not isinstance(component_ref, Mapping):
        return
    for forbidden in ("spec", "text"):
        if forbidden in component_ref:
            raise SchemaValidationError(
                f"{loc} must be a pure reference; inline {forbidden!r} is "
                "forbidden in a dehydrated pipeline (use url/digest/name)."
            )


def _check_argument_refs(
    value: Any,
    task_ids: set[str],
    input_names: set[str],
    *,
    loc: str,
) -> None:
    """Validate the graph references inside one ArgumentValue.

    * ``taskOutput.taskId`` must reference an emitted task id.
    * ``graphInput.inputName`` must reference a declared top-level input.
    """
    if not isinstance(value, Mapping):
        return
    task_output = value.get("taskOutput")
    if isinstance(task_output, Mapping):
        task_id = task_output.get("taskId")
        if task_id not in task_ids:
            raise SchemaValidationError(
                f"{loc}: taskOutput.taskId {task_id!r} does not reference an "
                f"emitted task. Known task ids: {sorted(task_ids)}."
            )
    graph_input = value.get("graphInput")
    if isinstance(graph_input, Mapping):
        input_name = graph_input.get("inputName")
        if input_name not in input_names:
            raise SchemaValidationError(
                f"{loc}: graphInput.inputName {input_name!r} does not reference "
                f"a declared top-level input. Declared inputs: "
                f"{sorted(input_names)}."
            )


def _validate_semantics(data: Mapping[str, Any]) -> None:
    """Run the dehydrated semantic checks. Assumes ``data`` already passed
    :func:`validate_dehydrated_data` (so the structure is well-formed)."""
    implementation = data.get("implementation", {})
    graph = implementation.get("graph", {}) if isinstance(implementation, Mapping) else {}
    tasks = graph.get("tasks", {}) if isinstance(graph, Mapping) else {}
    task_ids = set(tasks.keys()) if isinstance(tasks, Mapping) else set()

    input_names = _declared_names(data.get("inputs"))
    output_names = _declared_names(data.get("outputs"))
    outputs_present = "outputs" in data

    if isinstance(tasks, Mapping):
        for task_id, task in tasks.items():
            if not isinstance(task, Mapping):
                continue
            _assert_pure_component_ref(
                task.get("componentRef"), f"tasks.{task_id}.componentRef"
            )
            arguments = task.get("arguments")
            if isinstance(arguments, Mapping):
                for arg_name, value in arguments.items():
                    _check_argument_refs(
                        value,
                        task_ids,
                        input_names,
                        loc=f"tasks.{task_id}.arguments.{arg_name}",
                    )

    output_values = graph.get("outputValues") if isinstance(graph, Mapping) else None
    if isinstance(output_values, Mapping):
        for out_key, value in output_values.items():
            _check_argument_refs(
                value, task_ids, input_names, loc=f"outputValues.{out_key}"
            )
            if outputs_present and out_key not in output_names:
                raise SchemaValidationError(
                    f"outputValues key {out_key!r} does not correspond to any "
                    f"declared top-level output. Declared outputs: "
                    f"{sorted(output_names)}."
                )

    metadata = data.get("metadata")
    if isinstance(metadata, Mapping):
        annotations = metadata.get("annotations")
        if isinstance(annotations, Mapping):
            for key, value in annotations.items():
                # bool is a subclass of int, so it is covered by int.
                if not (value is None or isinstance(value, (str, int, float))):
                    raise SchemaValidationError(
                        f"metadata.annotations[{key!r}] must be a scalar "
                        f"(str/number/bool) or null, got "
                        f"{type(value).__name__!r}."
                    )


def validate_dehydrated_pipeline(
    data: Mapping[str, Any],
    exempt_paths: Collection[str] = (),
) -> None:
    """Strictly validate a *dehydrated* pipeline dict.

    Runs, in order:

    1. a top-level guard — reject ``template_file`` and any top-level key
       outside the schema-allowed set (leaked compile-time config);
    2. JSON-Schema (Draft 2020-12) structural validation
       (:func:`validate_dehydrated_data`);
    3. the no-template-delimiter output contract
       (:func:`assert_no_template_delimiters`);
    4. semantic checks: ``taskOutput.taskId`` / ``graphInput.inputName``
       existence, ``outputValues`` ↔ ``outputs`` correspondence, scalar
       metadata annotations, and pure componentRefs.

    Args:
        data: the dehydrated pipeline dict to validate.
        exempt_paths: JSON paths whose template delimiters are legitimate
            RUNTIME placeholders (authored via
            :func:`tangle_cli.python_pipeline.raw`) and must be skipped
            by the no-template-delimiter guard in step 3. Defaults to no
            exemptions so existing callers are unaffected. See
            :func:`assert_no_template_delimiters`.

    Raises:
        SchemaValidationError: with a clear, specific message on the first
            violation found.
    """
    if not isinstance(data, Mapping):
        raise SchemaValidationError(
            f"dehydrated pipeline must be a mapping, got {type(data).__name__!r}."
        )
    if "template_file" in data:
        raise SchemaValidationError(
            "dehydrated pipeline must not contain a 'template_file' key — it is "
            "the final rendered form, not a Jinja template wrapper."
        )
    extra = set(data) - _ALLOWED_TOP_LEVEL_KEYS
    if extra:
        raise SchemaValidationError(
            "dehydrated pipeline has disallowed top-level key(s): "
            f"{sorted(extra)}. Allowed top-level keys: "
            f"{sorted(_ALLOWED_TOP_LEVEL_KEYS)}. Compile-time config is baked "
            "into raw string constants and is never emitted at the top level."
        )

    validate_dehydrated_data(data)
    assert_no_template_delimiters(data, exempt_paths)
    _validate_semantics(data)
