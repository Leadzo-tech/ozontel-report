import json
import traceback

from ozonetel_cdr import run as run_ozonetel_sync


def handler(event, context):
    full_backfill = bool((event or {}).get("full_backfill", False))
    try:
        result = run_ozonetel_sync(full_backfill=full_backfill)
        return result
    except Exception as exc:
        error = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        print(json.dumps({"level": "error", "error_type": type(exc).__name__, "error_message": error}))
        raise
