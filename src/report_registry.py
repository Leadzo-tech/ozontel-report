import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


REPORT_DIR = Path(__file__).resolve().parent.parent / "scheduled_reports"


class ReportSpecError(ValueError):
    pass


@dataclass(frozen=True)
class ReportSpec:
    raw: dict[str, Any]
    path: Path

    @property
    def report_name(self) -> str:
        return str(self.raw["report_name"])

    @property
    def enabled(self) -> bool:
        return bool(self.raw.get("enabled", True))

    @property
    def schedule_expression(self) -> str:
        return str(self.raw["schedule"]["expression"])

    @property
    def spec_hash(self) -> str:
        encoded = json.dumps(self.raw, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


def load_report_specs(report_dir: Path = REPORT_DIR) -> dict[str, ReportSpec]:
    specs: dict[str, ReportSpec] = {}
    for path in sorted(report_dir.glob("*.yaml")):
        with path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        spec = ReportSpec(raw=raw, path=path)
        validate_report_spec(spec)
        if spec.report_name in specs:
            raise ReportSpecError(f"duplicate report_name: {spec.report_name}")
        specs[spec.report_name] = spec
    return specs


def get_report_spec(report_name: str) -> ReportSpec:
    specs = load_report_specs()
    try:
        return specs[report_name]
    except KeyError as exc:
        raise ReportSpecError(f"unknown report_name: {report_name}") from exc


def validate_all(report_dir: Path = REPORT_DIR) -> list[ReportSpec]:
    return list(load_report_specs(report_dir).values())


def validate_report_spec(spec: ReportSpec) -> None:
    raw = spec.raw
    required = ["report_name", "enabled", "schedule", "ozonetel", "sheet", "notifications"]
    for key in required:
        if key not in raw:
            raise ReportSpecError(f"{spec.path}: missing required field {key}")

    report_name = raw["report_name"]
    if not isinstance(report_name, str) or not report_name.strip():
        raise ReportSpecError(f"{spec.path}: report_name must be a non-empty string")
    if not all(c.isalnum() or c in "-_" for c in report_name):
        raise ReportSpecError(f"{spec.path}: report_name may contain only letters, numbers, '-' and '_'")

    schedule = raw["schedule"]
    if not isinstance(schedule, dict) or not schedule.get("expression"):
        raise ReportSpecError(f"{spec.path}: schedule.expression is required")
    expression = str(schedule["expression"])
    if not (expression.startswith("cron(") or expression.startswith("rate(")):
        raise ReportSpecError(f"{spec.path}: schedule.expression must be EventBridge cron(...) or rate(...)")

    ozonetel = raw["ozonetel"]
    if not ozonetel.get("endpoint"):
        raise ReportSpecError(f"{spec.path}: ozonetel.endpoint is required")
    for key in ["days_back_routine", "days_back_full", "rate_limit_sleep_seconds"]:
        value = ozonetel.get(key)
        if not isinstance(value, int) or value < 1:
            raise ReportSpecError(f"{spec.path}: ozonetel.{key} must be a positive integer")
    # Ozonetel only retains 15 days of CDRs; asking for more silently returns
    # nothing for the missing days rather than erroring, so catch it here.
    if ozonetel["days_back_full"] > 15:
        raise ReportSpecError(
            f"{spec.path}: ozonetel.days_back_full cannot exceed 15 — Ozonetel only retains 15 days of CDRs"
        )
    if ozonetel["days_back_routine"] > ozonetel["days_back_full"]:
        raise ReportSpecError(f"{spec.path}: ozonetel.days_back_routine cannot exceed days_back_full")

    sheet = raw["sheet"]
    for key in ["spreadsheet_id", "worksheet_name", "start_cell"]:
        if not sheet.get(key):
            raise ReportSpecError(f"{spec.path}: sheet.{key} is required")

    notifications = raw["notifications"]
    if not notifications.get("slack_webhook_param"):
        raise ReportSpecError(f"{spec.path}: notifications.slack_webhook_param is required")
