import os
import traceback
from typing import Any

from src.logging_utils import log_event
from src.ozonetel_cdr import run as run_ozonetel_sync
from src.report_registry import get_report_spec
from src.secrets import get_optional_parameter


def handle(event: dict[str, Any], context: Any) -> dict[str, Any]:
    report_name = (event or {}).get("report_name")
    if not report_name:
        raise ValueError("event.report_name is required")

    environment = (event or {}).get("environment") or os.environ.get("ENVIRONMENT", "prod")
    git_sha = os.environ.get("GIT_SHA", "unknown")
    full_backfill = bool((event or {}).get("full_backfill", False))

    spec = get_report_spec(report_name)
    log_context = {
        "report_name": report_name,
        "environment": environment,
        "git_sha": git_sha,
        "spec_hash": spec.spec_hash,
        "full_backfill": full_backfill,
    }

    if not spec.enabled:
        log_event("disabled", **log_context)
        return {"status": "disabled", "report_name": report_name}

    api_key = os.environ.get("OZONETEL_API_KEY")
    username = os.environ.get("OZONETEL_USERNAME")
    if not api_key or not username:
        raise RuntimeError("Missing OZONETEL_API_KEY / OZONETEL_USERNAME environment variables")

    log_event("start", **log_context)
    try:
        result = run_ozonetel_sync(spec.raw, api_key, username, full_backfill=full_backfill)
        log_event("success", rows_written=result["rows_written"], **log_context)
        return {"report_name": report_name, **result}
    except Exception as exc:
        error = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        log_event("failure", error_type=type(exc).__name__, error_message=error, **log_context)
        _send_failure_alert(spec.raw, {**log_context, "error": error})
        raise


def _send_failure_alert(spec_raw: dict[str, Any], payload: dict[str, Any]) -> None:
    """Best-effort Slack alert. A missing webhook param is a normal
    'not configured' state, and an alert failure must never mask the real
    error that triggered it."""
    from src.slack import send_failure_alert

    try:
        webhook_param = spec_raw["notifications"].get("slack_webhook_param")
        webhook_url = get_optional_parameter(webhook_param) if webhook_param else None
        if webhook_url:
            send_failure_alert(webhook_url, payload)
        else:
            log_event("slack_not_configured", webhook_param=webhook_param)
    except Exception as alert_exc:
        log_event(
            "failure_alert_error",
            error_type=type(alert_exc).__name__,
            error_message=str(alert_exc),
        )
