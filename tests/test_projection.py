from pathlib import Path

import pytest

from src.ozonetel_cdr import _project
from src.report_registry import ReportSpecError, load_report_specs
from tests.test_report_registry import VALID_SPEC, _write


def test_projection_selects_renames_and_orders_in_one_pass():
    records = [{"CallID": 1, "TalkTime": "00:00:58", "Junk": "drop me"}]
    projected = _project(records, {"Call ID": "CallID", "Talk Time": "TalkTime"})
    assert projected == [{"Call ID": 1, "Talk Time": "00:00:58"}]
    assert list(projected[0]) == ["Call ID", "Talk Time"]


def test_projection_fills_missing_source_fields_with_empty_string():
    projected = _project([{"CallID": 1}], {"Call ID": "CallID", "Rating": "Rating"})
    assert projected == [{"Call ID": 1, "Rating": ""}]


def test_shipped_spec_projects_38_of_the_47_ozonetel_fields():
    spec = load_report_specs()["ozonetel-cdr-sync"]
    projection = spec.raw["ozonetel"]["projection"]
    assert len(projection) == 38
    assert "UCID" not in projection and "Rating" not in projection
    assert "AgentID" not in projection
    assert list(projection)[0] == "CallID"
    assert list(projection)[-1] == "WrapUpStartTime"


def test_rejects_projection_that_is_not_a_mapping(tmp_path: Path):
    body = VALID_SPEC.replace(
        "  rate_limit_sleep_seconds: 31\n",
        "  rate_limit_sleep_seconds: 31\n  projection: [CallID]\n",
    )
    with pytest.raises(ReportSpecError, match="projection"):
        load_report_specs(_write(tmp_path, body))


def test_rejects_projection_and_preferred_order_together(tmp_path: Path):
    body = VALID_SPEC.replace(
        "  rate_limit_sleep_seconds: 31\n",
        "  rate_limit_sleep_seconds: 31\n  projection: {Call ID: CallID}\n",
    ).replace(
        "  start_cell: A1\n",
        "  start_cell: A1\n  columns:\n    preferred_order: [CallID]\n",
    )
    with pytest.raises(ReportSpecError, match="not both"):
        load_report_specs(_write(tmp_path, body))
