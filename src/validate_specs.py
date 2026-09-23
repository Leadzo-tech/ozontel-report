import json

from src.report_registry import validate_all


def main() -> int:
    specs = validate_all()
    print(json.dumps({"validated": [spec.report_name for spec in specs]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
