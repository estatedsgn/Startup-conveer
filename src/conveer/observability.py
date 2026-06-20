"""LangSmith observability wiring.

Provides a `traceable` decorator that is a no-op when LangSmith isn't installed
or tracing is disabled, so the rest of the code never has to special-case it.
"""

from __future__ import annotations

import os
from typing import Any, Callable


def tracing_enabled() -> bool:
    return os.getenv("LANGSMITH_TRACING", "").lower() in {"1", "true", "yes"}


def traceable(*d_args: Any, **d_kwargs: Any) -> Callable:
    """Decorator wrapper around langsmith.traceable with a safe fallback."""

    def decorator(func: Callable) -> Callable:
        if not tracing_enabled():
            return func
        try:
            from langsmith import traceable as ls_traceable
        except Exception:  # pragma: no cover - langsmith optional at runtime
            return func
        return ls_traceable(*d_args, **d_kwargs)(func)

    # Support both @traceable and @traceable(name=...)
    if len(d_args) == 1 and callable(d_args[0]) and not d_kwargs:
        func, d_args = d_args[0], ()
        return decorator(func)
    return decorator
