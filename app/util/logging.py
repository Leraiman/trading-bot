import logging
import os
import sys
from pythonjsonlogger import jsonlogger

def setup_logging() -> None:
    """
    Configure JSON logging via logging.basicConfig(...).
    Level from LOG_LEVEL env (default INFO).
    """
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, log_level, logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    fmt = jsonlogger.JsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s %(filename)s %(lineno)d"
    )
    handler.setFormatter(fmt)

    root = logging.getLogger()
    # Reset existing handlers in case of reload
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(level)

    # Less noisy third-party loggers (optional)
    for noisy in ("uvicorn", "uvicorn.error", "uvicorn.access", "asyncio", "httpx"):
        logging.getLogger(noisy).setLevel(level)
