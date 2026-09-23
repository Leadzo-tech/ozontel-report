from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "ozonetel_cdr.yaml"


def _load_config():
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_config_has_required_sheet_fields():
    config = _load_config()
    sheet = config["sheet"]
    for key in ("spreadsheet_id", "worksheet_name", "start_cell"):
        assert sheet.get(key), f"sheet.{key} is required"


def test_config_has_required_pull_window_fields():
    config = _load_config()
    window = config["pull_window"]
    for key in ("days_back_full", "days_back_routine", "rate_limit_sleep_seconds"):
        assert isinstance(window.get(key), int) and window[key] > 0, (
            f"pull_window.{key} must be a positive integer"
        )


def test_ozonetel_cdr_module_loads_config_successfully():
    import ozonetel_cdr

    assert ozonetel_cdr.SHEET_SPEC["worksheet_name"] == "Ozonetel"
    assert ozonetel_cdr.DAYS_BACK_ROUTINE > 0
