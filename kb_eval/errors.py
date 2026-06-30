"""Shared exceptions for the knowledge-base evaluation package."""

from __future__ import annotations


class EvalError(RuntimeError):
    """User-facing evaluation error."""


class FatalEvalError(EvalError):
    """Run-level evaluation error that should stop the current run."""
