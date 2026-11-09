"""LangSmith tracing helpers.

Tracing is opt-in: set LANGSMITH_API_KEY (or LANGCHAIN_API_KEY) and LANGSMITH_TRACING=true.
If the SDK or credentials are missing, decorators are no-ops and the extractor still runs.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("extractor")

_SETUP_DONE = False


def _load_dotenv() -> None:
    """Load .env into os.environ without overwriting variables already set."""
    roots = [Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env"]
    seen = set()
    for candidate in roots:
        try:
            path = candidate.resolve()
        except OSError:
            continue
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


def tracing_enabled() -> bool:
    if os.environ.get("LANGSMITH_TRACING", "").lower() in ("0", "false", "no"):
        return False
    return bool(os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY"))


def setup_tracing() -> bool:
    """Configure LangSmith env vars. Returns True only when a key is present."""
    global _SETUP_DONE
    if _SETUP_DONE:
        return tracing_enabled()
    _SETUP_DONE = True
    _load_dotenv()
    if not tracing_enabled():
        log.debug("LangSmith tracing off (no LANGSMITH_API_KEY / LANGCHAIN_API_KEY)")
        return False
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    os.environ.setdefault("LANGSMITH_PROJECT", "revenue-extractor")
    os.environ.setdefault("LANGCHAIN_PROJECT", os.environ["LANGSMITH_PROJECT"])
    log.info("LangSmith tracing enabled project=%s", os.environ.get("LANGSMITH_PROJECT"))
    return True


def traceable(*dargs, **dkwargs):
    """@traceable from langsmith when installed; otherwise a no-op decorator."""
    setup_tracing()
    ls_traceable = None
    if tracing_enabled():
        try:
            from langsmith import traceable as ls_traceable
        except ImportError:
            log.debug("langsmith package not installed; traces are local logs only")
            ls_traceable = None
    if ls_traceable is None:
        def deco(fn: Callable) -> Callable:
            return fn
        if dargs and callable(dargs[0]) and not dkwargs:
            return dargs[0]
        return deco
    return ls_traceable(*dargs, **dkwargs)


def log_run_io(inputs: dict[str, Any] | None = None, outputs: dict[str, Any] | None = None,
               error: str | None = None) -> None:
    """Attach extra I/O onto the current LangSmith run when one exists."""
    if not tracing_enabled():
        if error:
            log.error("pipeline error: %s", error)
        return
    try:
        from langsmith.run_helpers import get_current_run_tree
        run = get_current_run_tree()
        if run is None:
            return
        if inputs:
            run.add_inputs(inputs)
        if outputs:
            run.add_outputs(outputs)
        if error:
            run.error = error
    except Exception as exc:
        log.debug("could not attach LangSmith run I/O: %s", exc)
