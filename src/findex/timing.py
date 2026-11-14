"""`@timed`: log how long a call took.

Turn the log on with ``logging.basicConfig(level=logging.DEBUG)`` or
``python -m findex.search -v``; lines look like
``DEBUG findex.timing: Searcher.search took 1.8 ms``.
"""

from __future__ import annotations

import functools
import logging
import time
from collections.abc import Callable

log = logging.getLogger("findex.timing")


def timed[**P, R](func: Callable[P, R]) -> Callable[P, R]:
    """Decorator: log ``"<name> took <ms> ms"`` at DEBUG level after every call.

    Logged in ``finally``, so a call that raises is timed too. ``functools.wraps``
    copies ``__name__``, ``__qualname__``, ``__doc__`` and ``__wrapped__`` from
    `func`; without it every timed function would be called "wrapper" in logs,
    tracebacks and ``help()``.
    """

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        start = time.perf_counter()
        try:
            return func(*args, **kwargs)
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            log.debug("%s took %.1f ms", func.__qualname__, elapsed_ms)

    return wrapper
