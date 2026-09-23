from src.eventbridge_sync import rule_name, statement_id


def test_names_are_deterministic():
    assert rule_name("prod", "ozonetel-cdr-sync") == "oz-prod-ozonetel-cdr-sync"
    assert statement_id("oz-prod-ozonetel-cdr-sync") == "oz-prod-ozonetel-cdr-sync-invoke"
