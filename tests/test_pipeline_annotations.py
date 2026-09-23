"""Caller-supplied ROOT ``metadata.annotations`` (``pipeline_annotations``).

``compile_pipeline(..., pipeline_annotations={...})`` writes the compiled
pipeline's ROOT ``metadata.annotations`` block, so a downstream caller (in
practice a per-environment config file) can set descriptive metadata that the
authoring source does not hard-code. The contract these tests pin:

* **Per-key merge, caller wins.** Source ``@pipeline(annotations=...)`` keys the
  caller does not mention survive; a collision resolves to the caller's value.
* **Absent / empty is a no-op**, proven by BYTE identity of the whole bundle —
  ``{}`` is not a destructive clear of the source block.
* **Root only.** A ``subpipeline`` child inherits nothing, so its sidecar
  filename (``<slug>-<hash8>.yaml``, hashed over compile IDENTITY) and its
  bytes — and therefore its component digest — are untouched.
* **Hostile input is refused at parse time**, before any module is imported or
  any file is written, with a diagnostic that names the key and the type but
  NEVER echoes a value.
* **The value survives hydration**, which rewrites componentRefs and must not
  disturb root metadata.

One more thing is pinned here: annotation rules live in EXACTLY ONE place,
``schema_validation.check_annotations``, applied under two documented
policies — the same entry point a downstream config reader is meant to call,
with ``CALLER_ANNOTATION_POLICY``. The strict caller policy governs
the new ``pipeline_annotations`` input; the lenient document policy governs
every compiled or hand-authored pipeline and is deliberately unchanged
(scalar-or-null values, no key rules), so legacy YAML that validates today
still validates. The document check is also the backstop no path can bypass —
annotations that never went through the caller entry point still meet it. The
compiler contributes only orchestration and its precise error type.
"""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from tangle_cli import pipeline_compiler as pipeline_compiler_module
from tangle_cli.pipeline_compiler import PipelineCompiler, compile_pipeline
from tangle_cli.pipelines import compile_pipeline_file
from tangle_cli.python_pipeline.errors import (
    CompileError,
    InvalidPipelineAnnotationsError,
)
from tangle_cli.schema_validation import (
    CALLER_ANNOTATION_POLICY,
    DOCUMENT_ANNOTATION_POLICY,
    RESERVED_ANNOTATION_KEY_PREFIX,
    SchemaValidationError,
    check_annotations,
    validate_dehydrated_pipeline,
)

FIXTURES = Path(__file__).parent / "fixtures" / "python_pipeline"


def _validate_caller(annotations):
    """Validate exactly as the compiler does.

    The rules and the entry point live in the shared validation layer; the
    compiler contributes only the caller-facing error type, so tests call the
    shared API the same way a downstream config reader would.
    """
    return check_annotations(
        annotations,
        policy=CALLER_ANNOTATION_POLICY,
        error_cls=InvalidPipelineAnnotationsError,
    )

#: A ``@task`` pipeline (hermetic — no external component YAML to colocate)
#: whose ``@pipeline`` decorator arguments are substituted per fixture.
_SOURCE_TEMPLATE = textwrap.dedent(
    '''
    from tangle_cli.python_pipeline import In, Out, pipeline, task


    @task(image="python:3.12")
    def greet(greeting: str = "hi"):
        """Write a greeting.

        Metadata:
            Name: Greet
        """
        print(greeting)


    @pipeline(__DECORATOR_ARGS__)
    def annotated_pipeline(seed: In[str]) -> Out[str]:
        run_greet = greet(wait_for=seed)
        return run_greet
    '''
)

#: Source that already declares annotations, so merge/collision is observable.
_ANNOTATED_SOURCE = _SOURCE_TEMPLATE.replace(
    "__DECORATOR_ARGS__",
    '"Annotated Pipeline", '
    'annotations={"author": "source-author", "version": "1.0"}',
)

#: The same pipeline with NO source annotations, so the caller's mapping is the
#: only thing that can create the ``metadata`` block.
_UNANNOTATED_SOURCE = _SOURCE_TEMPLATE.replace(
    "__DECORATOR_ARGS__", '"Annotated Pipeline"'
)


def _write(tmp_path: Path, source: str, name: str = "pipeline.py") -> Path:
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    script = src_dir / name
    script.write_text(source, encoding="utf-8")
    return script


def _annotations_of(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data.get("metadata", {}).get("annotations", {})


def _bundle_bytes(root: Path) -> dict[str, bytes]:
    """Every file the compile wrote under ``root``'s directory, by name."""
    return {
        str(p.relative_to(root.parent)): p.read_bytes()
        for p in sorted(root.parent.rglob("*"))
        if p.is_file()
    }


# ---------------------------------------------------------------------------
# Merge semantics


def test_caller_annotations_merge_per_key_and_win_on_collision(tmp_path):
    script = _write(tmp_path, _ANNOTATED_SOURCE)
    out = tmp_path / "out" / "compiled.yaml"

    compile_pipeline(
        script,
        out,
        pipeline_annotations={"version": "2.0", "environment": "staging"},
    )

    annotations = _annotations_of(out)
    # Collision resolves to the caller; the untouched source key survives; the
    # caller's new key is added. Source-declared order comes first.
    assert annotations == {
        "author": "source-author",
        "version": "2.0",
        "environment": "staging",
    }
    assert list(annotations) == ["author", "version", "environment"]


def test_caller_annotations_create_the_metadata_block_when_source_has_none(tmp_path):
    script = _write(tmp_path, _UNANNOTATED_SOURCE)
    out = tmp_path / "out" / "compiled.yaml"

    compile_pipeline(script, out, pipeline_annotations={"environment": "staging"})

    assert _annotations_of(out) == {"environment": "staging"}


def test_the_caller_mapping_is_copied_not_aliased(tmp_path):
    """Mutating the caller's mapping after the call cannot reach the output."""
    script = _write(tmp_path, _UNANNOTATED_SOURCE)
    out = tmp_path / "out" / "compiled.yaml"
    supplied = {"environment": "staging"}

    compile_pipeline(script, out, pipeline_annotations=supplied)
    supplied["environment"] = "production"
    supplied["late"] = "addition"

    assert _annotations_of(out) == {"environment": "staging"}


@pytest.mark.parametrize("supplied", [None, {}], ids=["absent", "empty"])
def test_absent_or_empty_annotations_are_a_byte_identical_no_op(supplied, tmp_path):
    """``{}`` is a no-op, NOT a clear of the source block.

    Both compiles write to the SAME paths, so byte identity is over the whole
    bundle (root + ``@task`` sidecar) rather than over one file.
    """
    script = _write(tmp_path, _ANNOTATED_SOURCE)
    out = tmp_path / "out" / "compiled.yaml"

    compile_pipeline(script, out)
    baseline = _bundle_bytes(out)

    compile_pipeline(script, out, pipeline_annotations=supplied)

    assert _bundle_bytes(out) == baseline
    assert _annotations_of(out) == {"author": "source-author", "version": "1.0"}


# ---------------------------------------------------------------------------
# Root only: children inherit nothing, so their digests cannot move.


def test_a_subpipeline_child_neither_inherits_nor_changes_its_sidecar(tmp_path):
    """The child sidecar's NAME and BYTES are identical with and without the
    caller's annotations — the claim that component digests are untouched.

    The name is hashed over compile IDENTITY (``PipelineCompileKey``), so this
    also pins that the value stays out of ``overrides_fingerprint``; the bytes
    pin that no annotation leaked into the child graph.
    """
    script = FIXTURES / "subpipeline_pipeline.py"
    out = tmp_path / "out" / "compiled.yaml"

    baseline = compile_pipeline(script, out, pipeline_name="Parent Pipeline")
    child_baseline = baseline.subgraph_paths[0]
    child_baseline_bytes = child_baseline.read_bytes()

    annotated = compile_pipeline(
        script,
        out,
        pipeline_name="Parent Pipeline",
        pipeline_annotations={"environment": "staging"},
    )

    assert len(annotated.subgraph_paths) == 1
    child = annotated.subgraph_paths[0]
    assert child.name == child_baseline.name
    assert child.read_bytes() == child_baseline_bytes
    # Nothing landed in the child graph...
    assert _annotations_of(child) == {}
    # ...and everything landed in the root.
    assert _annotations_of(out) == {"environment": "staging"}


# ---------------------------------------------------------------------------
# Hydration preserves root metadata.


def test_root_annotations_survive_hydration(tmp_path):
    from tangle_cli.pipeline_hydrator import PipelineHydrator

    script = _write(tmp_path, _ANNOTATED_SOURCE)
    # Compiled NEXT TO the source: hydration refuses to execute a
    # ``local_from_python`` component whose Python file is not colocated with
    # the bundle (or otherwise allowlisted).
    out = script.parent / "compiled.yaml"
    compile_pipeline(
        script, out, pipeline_annotations={"version": "2.0", "environment": "staging"}
    )

    hydrated = PipelineHydrator(client=MagicMock()).hydrate_file(out)

    assert hydrated.data["metadata"]["annotations"] == {
        "author": "source-author",
        "version": "2.0",
        "environment": "staging",
    }


# ---------------------------------------------------------------------------
# Entry points: the handler and the facade carry the kwarg through.


def test_the_handler_and_the_facade_accept_the_kwarg(tmp_path):
    script = _write(tmp_path, _UNANNOTATED_SOURCE)

    handler_out = tmp_path / "handler" / "compiled.yaml"
    PipelineCompiler().compile_file(
        script, handler_out, pipeline_annotations={"environment": "staging"}
    )
    assert _annotations_of(handler_out) == {"environment": "staging"}

    facade_out = tmp_path / "facade" / "compiled.yaml"
    compile_pipeline_file(
        script, facade_out, pipeline_annotations={"environment": "production"}
    )
    assert _annotations_of(facade_out) == {"environment": "production"}


# ---------------------------------------------------------------------------
# Validation: hostile mappings are refused, and no diagnostic echoes a value.

#: Used as every rejected VALUE so one assertion can prove the message is
#: value-free regardless of which rule fired.
SECRET = "s3cret-annotation-payload"


@pytest.mark.parametrize(
    ("supplied", "expected_fragment"),
    [
        pytest.param(
            [("environment", "staging")],
            "must be a mapping of string keys to string values; got list",
            id="non-mapping",
        ),
        pytest.param(
            {1: SECRET},
            "keys must be strings; got a key of type int",
            id="non-string-key",
        ),
        pytest.param(
            {"": SECRET},
            "keys must not be empty",
            id="empty-key",
        ),
        pytest.param(
            {"environment": {"nested": SECRET}},
            "value for key 'environment' must be a string; got dict",
            id="mapping-value",
        ),
        pytest.param(
            {"enabled": True},
            "value for key 'enabled' must be a string; got bool",
            id="bool-value",
        ),
        pytest.param(
            {"replicas": 3},
            "value for key 'replicas' must be a string; got int",
            id="int-value",
        ),
        pytest.param(
            {"environment": None},
            "value for key 'environment' must be a string; got NoneType",
            id="none-value",
        ),
        pytest.param(
            {"system/owner": "platform"},
            "key 'system/owner' uses the reserved 'system/' prefix",
            id="reserved-prefix",
        ),
        pytest.param(
            {"environment": "{{ " + SECRET + " }}"},
            "value for key 'environment' contains the template delimiter '{{'",
            id="jinja-expression-value",
        ),
        pytest.param(
            {"environment": "{% if " + SECRET + " %}x{% endif %}"},
            "value for key 'environment' contains the template delimiter '{%'",
            id="jinja-statement-value",
        ),
        pytest.param(
            {"environment": "{# " + SECRET + " #}"},
            "value for key 'environment' contains the template delimiter '{#'",
            id="jinja-comment-value",
        ),
        pytest.param(
            {"{{ key }}": SECRET},
            "pipeline_annotations key '{{ key }}' contains the template "
            "delimiter '{{'",
            id="delimiter-in-key",
        ),
    ],
)
def test_a_hostile_annotations_mapping_is_refused_without_echoing_a_value(
    supplied, expected_fragment
):
    with pytest.raises(InvalidPipelineAnnotationsError) as exc:
        _validate_caller(supplied)

    message = str(exc.value)
    assert expected_fragment in message
    # Untrusted input: the offending VALUE never reaches the diagnostic.
    assert SECRET not in message


def test_the_annotations_error_is_a_compile_error():
    """Downstream callers may catch it precisely OR as a CompileError."""
    with pytest.raises(CompileError):
        _validate_caller({"": "x"})
    assert issubclass(InvalidPipelineAnnotationsError, CompileError)


def test_validation_accepts_an_empty_value_and_a_lone_brace():
    """Only the three template delimiters are refused, not any brace."""
    assert _validate_caller({"environment": ""}) == {"environment": ""}
    assert _validate_caller({"shape": "{json}"}) == {"shape": "{json}"}


@pytest.mark.parametrize("supplied", [None, {}], ids=["absent", "empty"])
def test_validation_normalizes_nothing_to_an_empty_dict(supplied):
    assert _validate_caller(supplied) == {}


def test_hostile_annotations_fail_before_the_script_is_read(tmp_path):
    """Parse-time validation: a bad mapping is refused before the compiler
    imports anything or writes anything, so the failure is about the
    annotations rather than about whatever else the compile would hit."""
    missing = tmp_path / "src" / "does-not-exist.py"
    out = tmp_path / "out" / "compiled.yaml"

    with pytest.raises(InvalidPipelineAnnotationsError) as exc:
        compile_pipeline(missing, out, pipeline_annotations={"": SECRET})

    assert "keys must not be empty" in str(exc.value)
    assert not out.exists()


def test_a_delimiter_value_is_refused_by_the_compiler_not_the_output_scan(tmp_path):
    """The compiled bundle must be fully hydrated; the caller-facing message
    names the annotation key rather than a JSON path in the emitted YAML."""
    script = _write(tmp_path, _UNANNOTATED_SOURCE)
    out = tmp_path / "out" / "compiled.yaml"

    with pytest.raises(InvalidPipelineAnnotationsError) as exc:
        compile_pipeline(
            script, out, pipeline_annotations={"environment": "{{ env_name }}"}
        )

    message = str(exc.value)
    assert "pipeline_annotations value for key 'environment'" in message
    assert "compiled pipeline output must contain no template delimiters" not in message
    assert not out.exists()


# ---------------------------------------------------------------------------
# One shared policy: both entry points go through check_annotations, and the
# lenient document rules are unchanged for legacy YAML.


def test_the_compiler_delegates_to_the_shared_check(monkeypatch, tmp_path):
    """The compiler owns no annotation rules: it calls the shared check with
    the caller policy and only supplies its own precise error type."""
    seen = {}

    def _spy(annotations, *, policy=None, error_cls=None):
        seen["annotations"] = annotations
        seen["policy"] = policy
        seen["error_cls"] = error_cls
        return {}

    monkeypatch.setattr(pipeline_compiler_module, "check_annotations", _spy)
    script = _write(tmp_path, _UNANNOTATED_SOURCE)

    compile_pipeline(
        script,
        tmp_path / "out" / "compiled.yaml",
        pipeline_annotations={"environment": "staging"},
    )

    assert seen["annotations"] == {"environment": "staging"}
    assert seen["policy"] is CALLER_ANNOTATION_POLICY
    assert seen["error_cls"] is InvalidPipelineAnnotationsError


def test_the_caller_policy_is_reusable_with_the_layer_default_error():
    """Same rules and messages whoever calls it. The DEFAULT error type is the
    validation layer's own, so a caller opts into a precise one."""
    with pytest.raises(SchemaValidationError) as exc:
        check_annotations({"replicas": 3}, policy=CALLER_ANNOTATION_POLICY)

    assert "pipeline_annotations value for key 'replicas' must be a string" in str(
        exc.value
    )
    assert check_annotations(
        {"environment": "staging"}, policy=CALLER_ANNOTATION_POLICY
    ) == {"environment": "staging"}


def test_the_reserved_prefix_has_one_definition():
    """The constant lives in the validation layer; the compiler keeps no copy."""
    assert RESERVED_ANNOTATION_KEY_PREFIX == "system/"
    assert not hasattr(pipeline_compiler_module, "RESERVED_ANNOTATION_PREFIX")


@pytest.mark.parametrize(
    "value",
    ["text", 3, 1.5, True, None],
    ids=["str", "int", "float", "bool", "null"],
)
def test_a_document_still_accepts_every_legacy_scalar_annotation(value):
    """COMPATIBILITY: hand-authored / legacy YAML carrying a non-string scalar
    annotation value must keep validating. The strict ``str -> str`` rule
    applies ONLY to the caller-supplied input surface."""
    document = {
        "name": "Legacy Pipeline",
        "metadata": {"annotations": {"version": value, "": value}},
        "implementation": {
            "graph": {
                "tasks": {
                    "Only Task": {
                        "componentRef": {"url": "https://example.test/c.yaml"}
                    }
                }
            }
        },
    }

    validate_dehydrated_pipeline(document)
    # The same value is refused on the caller surface — the asymmetry is the
    # point, and it is policy-driven rather than duplicated logic.
    if not isinstance(value, str):
        with pytest.raises(InvalidPipelineAnnotationsError):
            _validate_caller({"version": value})


def test_a_document_still_rejects_a_non_scalar_annotation_with_its_own_message():
    with pytest.raises(SchemaValidationError) as exc:
        check_annotations(
            {"version": {"nested": SECRET}}, policy=DOCUMENT_ANNOTATION_POLICY
        )

    message = str(exc.value)
    assert (
        "metadata.annotations['version'] must be a scalar (str/number/bool) "
        "or null, got 'dict'." in message
    )
    assert SECRET not in message


@pytest.mark.parametrize(
    "annotations",
    [
        {"system/owner": "platform"},
        {"": "empty-key"},
        {"environment": "{{ env }}"},
    ],
    ids=["reserved-prefix", "empty-key", "delimiter"],
)
def test_the_document_policy_is_not_tightened_by_the_caller_rules(annotations):
    """Rules that exist only for caller input must NOT start rejecting
    documents — the delimiter case still fails the OUTPUT scan, which is a
    pre-existing guard, but the annotations check itself accepts it."""
    assert check_annotations(annotations, policy=DOCUMENT_ANNOTATION_POLICY) == dict(
        annotations
    )
    with pytest.raises(InvalidPipelineAnnotationsError):
        _validate_caller(annotations)


def test_source_annotations_cannot_bypass_document_validation(tmp_path):
    """An annotation that never passes through the caller entry point — here
    one authored in ``@pipeline(annotations=...)`` — is still validated before
    anything is written, so no path reaches a written bundle unchecked.

    Which layer refuses it (JSON-Schema or the shared semantic policy, whose
    accepted value sets are identical by construction) is not the point and is
    deliberately not asserted; that the annotation is named, and that nothing
    is written, is. The compiler re-raises validation failures as
    :class:`CompileError`, so this is NOT the caller-input error type.
    """
    script = _write(
        tmp_path,
        _SOURCE_TEMPLATE.replace(
            "__DECORATOR_ARGS__",
            '"Annotated Pipeline", annotations={"owner": {"nested": "team"}}',
        ),
    )
    out = tmp_path / "out" / "compiled.yaml"

    with pytest.raises(CompileError) as exc:
        compile_pipeline(script, out)

    assert not isinstance(exc.value, InvalidPipelineAnnotationsError)
    assert "annotations" in str(exc.value)
    assert "owner" in str(exc.value)
    assert not out.exists()


def test_a_merged_caller_annotation_also_meets_the_document_check(tmp_path):
    """The merged result is validated as a document like any other, so the
    caller path is strict input validation layered ON TOP of the backstop,
    not a replacement for it."""
    script = _write(tmp_path, _UNANNOTATED_SOURCE)
    out = tmp_path / "out" / "compiled.yaml"

    compile_pipeline(script, out, pipeline_annotations={"environment": "staging"})

    document = yaml.safe_load(out.read_text(encoding="utf-8"))
    validate_dehydrated_pipeline(document)


# ---------------------------------------------------------------------------
# The existing fixtures still compile unchanged.


def test_an_ordinary_compile_is_unaffected(tmp_path):
    out = tmp_path / "compiled.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / "noop.yaml", out.parent / "noop.yaml")

    compile_pipeline(FIXTURES / "pipeline.py", out)

    data = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert "metadata" not in data
