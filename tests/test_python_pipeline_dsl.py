"""Tests for the ``tangle_cli.python_pipeline`` authoring DSL surface.

These lock the public import surface, the small pure helpers, the error
hierarchy, and the ``raw()`` contract so the DSL stays a stable, fully
OSS-native authoring layer (no ``tangle_deploy`` references) as the
compiler is migrated on top of it.
"""

from __future__ import annotations

import importlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

import tangle_cli.python_pipeline as pp
from tangle_cli.python_pipeline import (
    In,
    Out,
    Outputs,
    TaskEnv,
    pipeline,
    raw,
    ref,
    registered,
    subpipeline,
    task,
)
from tangle_cli.python_pipeline.errors import (
    AmbiguousTaskIdError,
    CompileError,
    InvalidArgumentTypeError,
    MissingRequiredInputError,
    UnknownCfgKeyError,
)
from tangle_cli.python_pipeline.ids import snake_to_title_case
from tangle_cli.python_pipeline.raw import Raw

_DSL_DIR = Path(pp.__file__).parent

# ============================================================================
# Public import surface
# ============================================================================


class TestPublicSurface:
    """The documented authoring entry points and ``__all__``."""

    def test_all_names_are_exported(self):
        assert set(pp.__all__) == {
            "pipeline",
            "task",
            "registered",
            "ref",
            "raw",
            "subpipeline",
            "TaskEnv",
            "In",
            "Out",
            "Outputs",
        }

    def test_every_all_name_is_present_on_module(self):
        for name in pp.__all__:
            assert hasattr(pp, name), f"missing export: {name}"

    def test_from_import_yields_identical_objects(self):
        # Identity matters: the compiler dispatches on ``isinstance`` against
        # the classes these decorators produce, so ``from ... import`` and
        # attribute access must resolve to the SAME objects (the linchpin for
        # the tangle_deploy re-export shim in a later phase).
        assert pipeline is pp.pipeline
        assert task is pp.task
        assert registered is pp.registered
        assert ref is pp.ref
        assert raw is pp.raw
        assert subpipeline is pp.subpipeline
        assert TaskEnv is pp.TaskEnv
        assert In is pp.In
        assert Out is pp.Out
        assert Outputs is pp.Outputs

    def test_cfg_is_not_a_top_level_export(self):
        # ``cfg`` is injected by the framework at trace time, never imported.
        assert "cfg" not in pp.__all__
        assert not hasattr(pp, "cfg") or "cfg" not in set(pp.__all__)

    def test_import_is_light(self):
        # The package docstring promises importing it does not eagerly pull in
        # the heavy codegen module. Re-import in a clean module state and check
        # the codegen module was not dragged in transitively.
        for mod in list(sys.modules):
            if mod == "tangle_cli.component_generator":
                del sys.modules[mod]
        importlib.reload(importlib.import_module("tangle_cli.python_pipeline"))
        assert "tangle_cli.component_generator" not in sys.modules


class TestNoInternalReferences:
    """The OSS authoring + compile surface must be free of internal
    references — neither the downstream package (``tangle_deploy`` /
    ``tangle-deploy``) nor the internal products/infra it was carved out of
    (Oasis, UPI, Comet, ``areas-ml*`` image paths). The dependency points
    inward: a downstream package registers its own authoring path via
    ``register_authoring_import_module`` — OSS never names it, and OSS
    docstrings/examples never name an internal product. A leaked ref here
    would surface in a user-facing CompileError, as one did before this
    guard was widened."""

    # Substring terms: unambiguous, safe to match anywhere in the text.
    _INTERNAL_SUBSTRINGS = ("tangle_deploy", "tangle-deploy", "areas-ml", "areas/ml")
    # Word-boundary terms: short/common enough that a bare substring match
    # would false-positive (e.g. "upi" inside "deduping"), so match only as a
    # standalone, case-insensitive word.
    _INTERNAL_WORDS = ("oasis", "upi", "comet")

    def test_no_tangle_deploy_references_in_source(self):
        # Scan the whole vendored DSL package plus the sibling modules that make
        # up the OSS compile surface: the authoring-import strip driver
        # (``component_from_func.py``) and the ported compiler + its schema
        # validator + the ``pipelines`` facade/CLI. All must stay decoupled from
        # the downstream package name and from internal product names.
        scanned = list(_DSL_DIR.glob("*.py"))
        scanned += [
            _DSL_DIR.parent / name
            for name in (
                "component_from_func.py",
                "pipeline_compiler.py",
                "schema_validation.py",
                "pipelines.py",
                "pipelines_cli.py",
            )
        ]
        word_re = re.compile(
            r"\b(" + "|".join(self._INTERNAL_WORDS) + r")\b", re.IGNORECASE
        )
        offenders = {}
        for path in scanned:
            text = path.read_text(encoding="utf-8")
            hits = [term for term in self._INTERNAL_SUBSTRINGS if term in text]
            hits += sorted({m.group(0).lower() for m in word_re.finditer(text)})
            if hits:
                offenders[path.name] = hits
        assert offenders == {}, f"internal references found: {offenders}"


# ============================================================================
# ids.snake_to_title_case
# ============================================================================


class TestSnakeToTitleCase:
    @pytest.mark.parametrize(
        ("raw_name", "expected"),
        [
            ("build_quality_tables", "Build Quality Tables"),
            ("foo", "Foo"),
            ("a__b", "A B"),  # empty segments collapse
            ("", ""),
            ("GPU", "Gpu"),  # str.capitalize lowercases trailing letters
            ("already_Title", "Already Title"),
        ],
    )
    def test_conversion(self, raw_name, expected):
        assert snake_to_title_case(raw_name) == expected


# ============================================================================
# errors hierarchy
# ============================================================================


class TestErrorHierarchy:
    @pytest.mark.parametrize(
        "exc_cls",
        [
            UnknownCfgKeyError,
            MissingRequiredInputError,
            AmbiguousTaskIdError,
            InvalidArgumentTypeError,
        ],
    )
    def test_all_authoring_errors_subclass_compile_error(self, exc_cls):
        assert issubclass(exc_cls, CompileError)
        assert issubclass(exc_cls, Exception)

    def test_compile_error_is_catchable(self):
        with pytest.raises(CompileError):
            raise UnknownCfgKeyError("nope")


# ============================================================================
# types markers (In / Out / Outputs)
# ============================================================================


class TestTypeMarkers:
    def test_in_and_out_are_subscriptable(self):
        # Pure annotation markers — subscripting must not raise.
        assert In[str] is not None
        assert Out[int] is not None

    def test_outputs_is_usable_as_dataclass_base(self):
        @dataclass(frozen=True)
        class JudgeOutputs(Outputs):
            rows_written: Out[str]
            run_id: Out[str]

        inst = JudgeOutputs(rows_written="a", run_id="b")
        assert inst.rows_written == "a"
        assert inst.run_id == "b"
        assert isinstance(inst, Outputs)


# ============================================================================
# raw() contract
# ============================================================================


class TestRaw:
    def test_wraps_string_as_raw(self):
        r = raw("SELECT * FROM `{{input_1}}`")
        assert isinstance(r, Raw)
        assert r.value == "SELECT * FROM `{{input_1}}`"

    def test_equality_and_hash(self):
        assert raw("{{x}}") == raw("{{x}}")
        assert hash(raw("{{x}}")) == hash(raw("{{x}}"))
        assert raw("{{x}}") != raw("{{y}}")

    def test_rejects_non_string(self):
        with pytest.raises(TypeError):
            raw(123)  # type: ignore[arg-type]

    @pytest.mark.parametrize("value", ["{% if x %}", "{# comment #}"])
    def test_rejects_jinja_tokens(self, value):
        # Only ``{{...}}`` runtime sentinels are legitimate; Jinja
        # statement/comment tokens are compile-time and must be rejected.
        with pytest.raises(ValueError):
            raw(value)

    def test_allows_plain_runtime_sentinel(self):
        # A bare ``{{...}}`` with no Jinja statement/comment token is fine.
        assert raw("{{input_1}}").value == "{{input_1}}"
