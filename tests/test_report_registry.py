from pathlib import Path

import pytest

from src.report_registry import ReportSpecError, load_report_specs

VALID_SPEC = """
report_name: sample
enabled: true
schedule:
  expression: rate(5 minutes)
ozonetel:
  endpoint: https://in1-ccaas-api.ozonetel.com/ca_reports/fetchCDRDetails
  days_back_routine: 2
  days_back_full: 15
  rate_limit_sleep_seconds: 31
sheet:
  spreadsheet_id: abc
  worksheet_name: Ozonetel
  start_cell: A1
notifications:
  slack_webhook_param: /leadzo/ozonetel-cdr-proxy-poc/poc/slack/default-webhook-url
"""


def _write(tmp_path: Path, body: str, name: str = "spec.yaml") -> Path:
    (tmp_path / name).write_text(body, encoding="utf-8")
    return tmp_path


def test_loads_the_real_shipped_spec():
    specs = load_report_specs()
    assert "ozonetel-cdr-sync" in specs
    spec = specs["ozonetel-cdr-sync"]
    assert spec.schedule_expression == "cron(30 * * * ? *)"
    assert spec.raw["sheet"]["worksheet_name"] == "Ozonetel"


def test_spec_hash_changes_with_content(tmp_path: Path):
    first = load_report_specs(_write(tmp_path, VALID_SPEC))["sample"].spec_hash
    second = load_report_specs(
        _write(tmp_path, VALID_SPEC.replace("days_back_routine: 2", "days_back_routine: 3"))
    )["sample"].spec_hash
    assert first != second


def test_rejects_non_eventbridge_schedule(tmp_path: Path):
    body = VALID_SPEC.replace("expression: rate(5 minutes)", "expression: every 5 minutes")
    with pytest.raises(ReportSpecError, match="cron"):
        load_report_specs(_write(tmp_path, body))


def test_rejects_missing_sheet_field(tmp_path: Path):
    body = VALID_SPEC.replace("  worksheet_name: Ozonetel\n", "")
    with pytest.raises(ReportSpecError, match="worksheet_name"):
        load_report_specs(_write(tmp_path, body))


def test_rejects_window_beyond_ozonetel_retention(tmp_path: Path):
    body = VALID_SPEC.replace("days_back_full: 15", "days_back_full: 30")
    with pytest.raises(ReportSpecError, match="15 days"):
        load_report_specs(_write(tmp_path, body))


def test_rejects_routine_window_larger_than_full(tmp_path: Path):
    body = VALID_SPEC.replace("days_back_routine: 2", "days_back_routine: 20")
    with pytest.raises(ReportSpecError):
        load_report_specs(_write(tmp_path, body))


def test_rejects_duplicate_report_names(tmp_path: Path):
    _write(tmp_path, VALID_SPEC, "a.yaml")
    _write(tmp_path, VALID_SPEC, "b.yaml")
    with pytest.raises(ReportSpecError, match="duplicate"):
        load_report_specs(tmp_path)
