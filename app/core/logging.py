"""
app/core/logging.py — Centralised logging configuration.

Import and call configure_logging() once at application startup.
"""
import logging


def configure_logging(level: int = logging.INFO) -> None:
    """Apply a consistent log format across the whole application."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


# Module-level logger for callers that just do `from app.core.logging import logger`.
logger = logging.getLogger("coconut_detector")
