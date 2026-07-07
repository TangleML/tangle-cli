"""Compile-time error hierarchy.

All errors raised by the Python pipeline authoring layer before/during
``tangle-deploy pipeline compile from-python`` are subclasses of :class:`CompileError`.
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
