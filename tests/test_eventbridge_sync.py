from pathlib import Path

from src.eventbridge_sync import EventBridgeReconciler, rule_name, statement_id
from src.report_registry import ReportSpec


def test_names_are_deterministic():
    assert rule_name("prod", "ozonetel-cdr-sync") == "oz-prod-ozonetel-cdr-sync"
    assert statement_id("oz-prod-ozonetel-cdr-sync") == "oz-prod-ozonetel-cdr-sync-invoke"


def test_extra_runs_become_their_own_rules_with_extra_input():
    spec = ReportSpec(
        raw={
            "report_name": "r",
            "enabled": True,
            "schedule": {
                "expression": "rate(1 hour)",
                "extra_runs": [{"id": "bf", "expression": "cron(5 11 23 9 ? 2026)", "input": {"replace": True}}],
            },
        },
        path=Path("r.yaml"),
    )
    reconciler = EventBridgeReconciler.__new__(EventBridgeReconciler)
    reconciler.environment = "prod"
    desired = reconciler.desired_rules({"r": spec})
    assert desired["oz-prod-r"].expression == "rate(1 hour)"
    assert desired["oz-prod-r"].extra_input == {}
    assert desired["oz-prod-r--bf"].expression == "cron(5 11 23 9 ? 2026)"
    assert desired["oz-prod-r--bf"].extra_input == {"replace": True}
