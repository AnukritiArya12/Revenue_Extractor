"""Optional LangSmith tracing. Safe to import when langsmith is not installed."""
from src.observability.tracing import setup_tracing, traceable, tracing_enabled

__all__ = ["setup_tracing", "traceable", "tracing_enabled"]
