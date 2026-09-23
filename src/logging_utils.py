import json
import logging
import os
from datetime import datetime, timezone
from typing import Any


logger = logging.getLogger("ozonetel_report")
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_event(phase: str, **fields: Any) -> None:
    payload = {
        "phase": phase,
        "timestamp": utc_now_iso(),
        **fields,
    }
    logger.info(json.dumps(payload, default=str, sort_keys=True))
