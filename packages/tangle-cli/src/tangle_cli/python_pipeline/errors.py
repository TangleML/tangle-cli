"""Compile-time error hierarchy.

All errors raised by the Python pipeline authoring layer before/during
``tangle sdk pipelines compile`` are subclasses of :class:`CompileError`.
They should include enough context for a user to fix the underlying issue
without reading framework source: file:line of the offending call, the
relevant primitive name, and a suggested remedy.
"""
from __future__ import annotations


class CompileError(Exception):
    """Base class for all compile-time failures in the authoring layer."""


class UnknownCfgKeyError(CompileError):
    """Raised on ``cfg.<unknown_key>`` access."""


class MissingRequiredInputError(CompileError):
    """Raised when a required In[T] graph input is missing at trace time."""


class AmbiguousTaskIdError(CompileError):
    """Raised when LHS-name inference for a task ID cannot be resolved."""


class InvalidArgumentTypeError(CompileError):
    """Raised on an argument value with no supported emit dispatch."""


class InvalidEditorLayoutError(CompileError):
    """Raised on a malformed value passed to the typed layout sugar.

    Covers ``.with_position`` coordinates and
    ``@pipeline(flow_direction=...)`` only; hand-written
    ``.with_annotations`` values still pass through unchecked.
    """


class InvalidGraphIoError(CompileError):
    """Raised on an unusable ``graph_input()`` / ``graph_output()`` call.

    Covers declaration outside a trace, duplicate names, fields the pipeline
    schema would reject, and constant graph outputs. Messages name the field,
    never the value.
    """


class InvalidPipelineAnnotationsError(CompileError):
    """Raised on a malformed caller-supplied ``pipeline_annotations`` mapping.

    A dedicated type because these annotations usually originate in a
    downstream CONFIG file: a caller that reads such a file can catch this
    precisely and re-raise with the config path and key attached, without
    broadly catching :class:`CompileError` and swallowing unrelated compile
    failures. Messages never echo an annotation value.
    """
