"""Structured logging configuration for OQIM Business."""

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)

# httpx logs full request URLs at INFO — Instagram token refresh/exchange
# carries access_token in query params (Meta's documented interface), so
# library request logs would leak 60-day credentials into pilot logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger("oqim_business")
logger.setLevel(logging.INFO)


def get_logger(name: str | None = None) -> logging.Logger:
    if name:
        return logging.getLogger(f"oqim_business.{name}")
    return logger
